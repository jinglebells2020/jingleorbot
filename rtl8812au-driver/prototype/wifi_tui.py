#!/usr/bin/env python3
"""
alfawifi — a terminal UI to drive the from-scratch RTL8812AU (Alfa AWUS036ACH)
userspace Wi-Fi driver: scan networks (2.4 + 5 GHz), connect to WPA2/open
networks, get a DHCP lease, and run a ping test — all on Apple Silicon macOS
with no kext and no entitlements.

Keys:  s=scan  Enter=connect  d=disconnect  p=ping test  q=quit
"""
import curses, sys, time, struct, os, hashlib, importlib, threading, socket, fcntl, subprocess, select, signal
import phy8812 as P
sys.path.insert(0,'.')
tx  = importlib.import_module('08_tx_inject')
asc = importlib.import_module('10_associate')
conn= importlib.import_module('11_connect')
br  = importlib.import_module('12_bridge')

OUR_MAC = asc.OUR_MAC          # must match the helper modules' RX filter
PN=[1]
PUB_DNS='8.8.8.8'

# ---------------- utun / routing / DNS (for full-traffic bridge; needs root) ----------------
AF_SYS_CONTROL=2; SYSPROTO_CONTROL=2
UTUN_CONTROL_NAME=b'com.apple.net.utun_control'; CTLIOCGINFO=0xC0644E03; UTUN_OPT_IFNAME=2
DNS_SVC='alfa-utun-dns'

def _sh(cmd): os.system(cmd+" >/dev/null 2>&1")
def utun_open():
    s=socket.socket(socket.AF_SYSTEM, socket.SOCK_DGRAM, SYSPROTO_CONTROL)
    info=fcntl.ioctl(s, CTLIOCGINFO, struct.pack('I96s',0,UTUN_CONTROL_NAME))
    s.connect((struct.unpack('I96s',info)[0], 0))
    name=s.getsockopt(SYSPROTO_CONTROL, UTUN_OPT_IFNAME, 256).rstrip(b'\x00').decode()
    return s, name
def dns_up(name, ip_s, gw_s):
    script=(f"d.init\nd.add ServerAddresses * {PUB_DNS} 1.1.1.1\nset State:/Network/Service/{DNS_SVC}/DNS\n"
            f"d.init\nd.add Addresses * {ip_s}\nd.add SubnetMasks * 255.255.255.255\n"
            f"d.add Router {gw_s}\nd.add InterfaceName {name}\nd.add OverridePrimary # 1\n"
            f"set State:/Network/Service/{DNS_SVC}/IPv4\nquit\n")
    subprocess.run(['scutil'], input=script, text=True)
    _sh("dscacheutil -flushcache; killall -HUP mDNSResponder")
def dns_down():
    subprocess.run(['scutil'], input=(f"remove State:/Network/Service/{DNS_SVC}/DNS\n"
                                      f"remove State:/Network/Service/{DNS_SVC}/IPv4\nquit\n"), text=True)

# ---------------- driver operations (parameterized) ----------------
def rx_frames(c, timeout):
    """Yield (fc, a1, a2, a3, plen, prot, hlen, pt_or_ccmp_start) for RX frames."""
    t0=time.time()
    while time.time()-t0<timeout:
        try: d=c.dev.read(0x81,16384,timeout=200)
        except Exception: return
        if not d: continue
        buf=bytes(d); i=0; n=len(buf)
        while i+24<=n:
            w0=struct.unpack_from('<I',buf,i)[0]; plen=w0&0x3FFF; dv=(w0>>16)&0xF; s=(w0>>24)&0x3
            if plen==0 or plen>8192: break
            w2=struct.unpack_from('<I',buf,i+8)[0]; h=i+24+dv*8+s
            if not((w2>>28)&1) and h+24<=n and h+plen<=n:
                yield buf, h, plen
            adv=(24+dv*8+s+plen+7)&~7
            if adv<=0: break
            i+=adv

def scan(c, cut_c, progress):
    """Scan 2.4 + 5 GHz, return {bssid: {ssid,band,ch,secured}}."""
    nets={}
    plan=[('2.4',ch) for ch in (1,6,11,2,3,4,5,7,8,9,10,12,13)] + \
         [('5',ch) for ch in (36,40,44,48,149,153,157,161,165,52,56,60,64)]
    for idx,(band,ch) in enumerate(plan):
        progress(f"scanning {band}GHz ch {ch} ({idx+1}/{len(plan)})  found {len(nets)}")
        if band=='2.4': P.switch_band_2g(c); P.set_channel_2g(c,2,ch,cut_c)
        else: P.switch_band_5g(c); P.set_channel_5g(c,2,ch,cut_c)
        P.lc_calibrate(c,cut_c); time.sleep(0.03)
        for _ in rx_frames(c,0.06): pass            # flush frames buffered from prev channel
        for buf,h,plen in rx_frames(c,0.4):
            fc=buf[h]
            if fc not in (0x80,0x50): continue     # beacon or probe-resp
            a3=buf[h+16:h+22]; bssid=a3.hex(':')
            cap=struct.unpack('<H',buf[h+24+10:h+24+12])[0] if h+36<=len(buf) else 0
            secured=bool(cap & 0x10)               # privacy bit
            p=h+24+12; end=min(len(buf),h+24+plen); ssid=None; real_ch=None
            while p+2<=end:                        # walk all IEs
                eid,el=buf[p],buf[p+1]
                if p+2+el>end: break
                if eid==0:                          # SSID
                    raw=bytes(buf[p+2:p+2+el])
                    if el==0: ssid='<hidden>'
                    elif all(32<=x<127 for x in raw): ssid=raw.decode()
                    else: ssid='<non-ascii>'
                elif eid==3 and el>=1:              # DS Parameter Set -> real channel
                    real_ch=buf[p+2]
                elif eid==61 and el>=1 and real_ch is None:   # HT Operation primary channel
                    real_ch=buf[p+2]
                p+=2+el
            if real_ch is None: real_ch=ch          # fall back to tuned channel
            rband='2.4' if real_ch<=14 else '5'     # band from actual channel
            if ssid and bssid not in nets:
                nets[bssid]={'ssid':ssid,'band':rband,'ch':real_ch,'secured':secured}
    return nets

def _send_enc(c, outs, tk, bssid, a3, eth, payload):
    hdr=bytes([0x08,0x41])+b'\x00\x00'+bssid+OUR_MAC+a3+b'\x00\x00'
    enc=conn.ccmp_encrypt(tk, hdr, bytes([0xaa,0xaa,0x03,0,0,0])+eth+payload, PN[0]); PN[0]+=1
    conn.send_data(c, outs, hdr+enc, sec=0)

def connect(c, cut_c, outs, net, bssid_bytes, psk, log):
    """auth+assoc(+4way if secured). Returns (tk,gtk,gtk_keyid) or None."""
    # The helper modules each captured a HARDCODED AP_BSSID at import time (the
    # original Capitol dev AP), and their receive filters (asc.recv_mgmt,
    # conn.recv_eapol, br.recv_dhcp) drop every frame whose a2 != that global.
    # So a connect to any *other* AP silently failed - auth/assoc/4way replies
    # were thrown away as "from the wrong BSSID". Point all three at the AP we
    # are actually joining before any receive happens.
    asc.AP_BSSID = bssid_bytes
    conn.AP_BSSID = bssid_bytes
    br.AP_BSSID = bssid_bytes
    ssid=net['ssid'].encode()
    if net['band']=='2.4': P.switch_band_2g(c); P.set_channel_2g(c,2,net['ch'],cut_c)
    else: P.switch_band_5g(c); P.set_channel_5g(c,2,net['ch'],cut_c)
    P.lc_calibrate(c,cut_c)
    asc.set_managed(c, OUR_MAC, bssid_bytes)
    log("authenticating...")
    ok=False
    for a in range(10):
        asc.send(c,outs, asc.mgmt_hdr(11,bssid_bytes,OUR_MAC,bssid_bytes,a)+struct.pack('<HHH',0,1,0))
        b=asc.recv_mgmt(c,11,0.4)
        if b and len(b)>=6 and struct.unpack('<HHH',b[:6])[1:]==(2,0): ok=True; break
    if not ok: log("auth failed"); return None
    log("associating...")
    rsn=conn.RSN_IE if net['secured'] else b''
    ab=struct.pack('<HH',0x0431 if net['secured'] else 0x0421,10)+bytes([0,len(ssid)])+ssid+\
       bytes([1,8,0x8c,0x12,0x98,0x24,0xb0,0x48,0x60,0x6c])+rsn
    aid=None
    for a in range(10):
        asc.send(c,outs, asc.mgmt_hdr(0,bssid_bytes,OUR_MAC,bssid_bytes,a)+ab)
        b=asc.recv_mgmt(c,1,0.4)
        if b and len(b)>=6 and struct.unpack('<HHH',b[:6])[1]==0: aid=struct.unpack('<HHH',b[:6])[2]&0x3fff; break
    if aid is None: log("assoc failed"); return None
    if not net['secured']:
        log(f"associated (open), AID={aid}"); return ('OPEN',None,0)
    log("WPA2 4-way handshake...")
    m1=conn.recv_eapol(c,2.5)
    if not m1: log("no EAPOL msg1"); return None
    ki1,rp1,anonce,_,_=m1; ver=ki1&0x7
    pmk=hashlib.pbkdf2_hmac('sha1',psk.encode(),ssid,4096,32)
    snonce=os.urandom(32)
    kck,kek,tk=conn.derive_ptk(pmk,bssid_bytes,OUR_MAC,anonce,snonce)
    conn.send_data(c,outs, conn.data_hdr(1,bssid_bytes,OUR_MAC,bssid_bytes)+conn.LLC_EAPOL+
                   conn.build_eapol(ver|0x008|0x100,rp1,snonce,conn.RSN_IE,kck=kck))
    m3=conn.recv_eapol(c,2.5)
    if not m3: log("no msg3 (wrong password?)"); return None
    ki3,rp3,n3,kd3,_=m3
    if n3!=anonce: anonce=n3; kck,kek,tk=conn.derive_ptk(pmk,bssid_bytes,OUR_MAC,anonce,snonce)
    gtk=None; gid=1
    try:
        iv,dec=conn.aes_unwrap(kek,kd3); p=0
        while p+2<=len(dec):
            e,dl=dec[p],dec[p+1]
            if e==0 or dl==0: break
            if e==0xdd and dec[p+2:p+5]==bytes([0,0x0f,0xac]) and dec[p+5]==1:
                gid=dec[p+6]&3; gtk=dec[p+8:p+2+dl]; break
            p+=2+dl
    except Exception: pass
    conn.send_data(c,outs, conn.data_hdr(1,bssid_bytes,OUR_MAC,bssid_bytes)+conn.LLC_EAPOL+
                   conn.build_eapol(ver|0x008|0x100|0x200,rp3,b'\x00'*32,b'',kck=kck))
    conn.cam_write_entry(c,0, conn.CAM_VALID|(conn.CAM_AES<<2)|0, bssid_bytes, tk)
    if gtk: conn.cam_write_entry(c,gid, conn.CAM_VALID|(conn.CAM_AES<<2)|gid, b'\xff'*6, gtk[:16])
    c.w32(conn.REG_SECCFG, 0xcc|(1<<8))
    c.w32(0x608, (1<<1)|(1<<2)|(1<<3)|(1<<13)|(1<<28))
    log("connected + keyed")
    return (tk,gtk,gid)

def dhcp_lease(c, outs, tk, bssid, log):
    xid=os.urandom(4)
    for _ in range(15):
        _send_enc(c,outs,tk,bssid,b'\xff'*6,b'\x08\x00', br.build_dhcp(OUR_MAC,xid,1))
        off=br.recv_dhcp(c,0.5)
        if off and off['type']==2:
            for _ in range(8):
                _send_enc(c,outs,tk,bssid,b'\xff'*6,b'\x08\x00', br.build_dhcp(OUR_MAC,xid,3,off['yiaddr'],off['server']))
                ack=br.recv_dhcp(c,0.5)
                if ack and ack['type']==5: return ack
    return None

def ping_test(c, outs, tk, bssid, our_ip, gw_ip, log):
    # resolve gateway MAC
    arp=bytes([0,1,8,0,6,4,0,1])+OUR_MAC+our_ip+b'\x00'*6+gw_ip; gw_mac=None
    for _ in range(20):
        _send_enc(c,outs,tk,bssid,b'\xff'*6,b'\x08\x06',arp)
        for buf,h,plen in rx_frames(c,0.3):
            if buf[h+1]>>6&1 and buf[h+10:h+16]==bssid:
                hl=26 if (buf[h]&0xF0)==0x80 else 24; pt=buf[h+hl+8:h+plen]
                if pt[6:8]==b'\x08\x06' and pt[14:16]==b'\x00\x02' and pt[16+6:16+10]==gw_ip:
                    gw_mac=bytes(pt[16:16+6]); break
        if gw_mac: break
    if not gw_mac: log("gateway ARP failed"); return 0
    got=0
    for seq in range(6):
        h=struct.pack('>BBHHH',8,0,0,0xbeef,seq)
        ic=struct.pack('>BBHHH',8,0,br.ip_csum(h+b'alfa-macos-driver!!!'),0xbeef,seq)+b'alfa-macos-driver!!!'
        ln=20+len(ic)
        ip=bytearray([0x45,0,(ln>>8)&0xff,ln&0xff,0x12,0x34,0,0,64,1,0,0])+our_ip+bytes([8,8,8,8])
        ip[10:12]=struct.pack('>H',br.ip_csum(bytes(ip)))
        _send_enc(c,outs,tk,bssid,gw_mac,b'\x08\x00',bytes(ip)+ic)
        for buf,hh,plen in rx_frames(c,0.4):
            if buf[hh+4:hh+10]==OUR_MAC and (buf[hh+1]>>6&1):
                hl=26 if (buf[hh]&0xF0)==0x80 else 24; pt=buf[hh+hl+8:hh+plen]
                if pt[6:8]==b'\x08\x00':
                    ipr=pt[8:]; ihl=(ipr[0]&0xf)*4
                    if ipr[9]==1 and bytes(ipr[12:16])==bytes([8,8,8,8]) and ipr[ihl]==0:
                        got+=1; log(f"ping 8.8.8.8 reply seq={seq} ttl={ipr[8]}"); break
    return got

def resolve_gw(c, outs, tk, bssid, our_ip, gw_ip, tries=30):
    arp=bytes([0,1,8,0,6,4,0,1])+OUR_MAC+our_ip+b'\x00'*6+gw_ip
    for _ in range(tries):
        _send_enc(c,outs,tk,bssid,b'\xff'*6,b'\x08\x06',arp)
        for buf,h,plen in rx_frames(c,0.3):
            if (buf[h+1]>>6&1) and buf[h+10:h+16]==bssid:
                hl=26 if (buf[h]&0xF0)==0x80 else 24; pt=buf[h+hl+8:h+plen]
                if pt[6:8]==b'\x08\x06' and pt[14:16]==b'\x00\x02' and pt[16+6:16+10]==gw_ip:
                    return bytes(pt[16:16+6])
    return None

# ---------------- curses UI ----------------
class App:
    def __init__(self):
        self.c=None; self.cut_c=0; self.outs=[]
        self.nets={}; self.order=[]; self.sel=0
        self.state='init'; self.status='initializing adapter...'
        self.conn=None; self.lease=None; self.cur_bssid=None; self.cur_ssid=None
        self.gw_mac=None; self.log=[]
        self.is_root=(os.geteuid()==0)
        self.bridge_on=False; self.utun=None; self.utun_name=None
        self.brun=None; self.bstats={'tx':0,'rx':0,'dup':0}

    def logline(self,s): self.log.append(s); self.log=self.log[-8:]

    def init_adapter(self):
        self.c=P.Chip()
        self.cut_c=tx.full_bringup(self.c)
        import usb.util as U
        intf=self.c.dev.get_active_configuration()[(0,0)]
        self.outs=[e.bEndpointAddress for e in intf
                   if U.endpoint_direction(e.bEndpointAddress)==U.ENDPOINT_OUT
                   and U.endpoint_type(e.bmAttributes)==U.ENDPOINT_TYPE_BULK]
        self.state='idle'; self.status='ready — press s to scan'

    def do_scan(self, scr):
        self.state='scanning'
        def prog(s): self.status=s; self.draw(scr)
        self.nets=scan(self.c,self.cut_c,prog)
        self.order=sorted(self.nets, key=lambda b:(self.nets[b]['band'],self.nets[b]['ssid'].lower()))
        self.sel=0; self.state='idle'; self.status=f"scan done — {len(self.nets)} networks"

    def do_connect(self, scr, psk):
        b=self.order[self.sel]; net=self.nets[b]; bb=bytes(int(x,16) for x in b.split(':'))
        self.state='connecting'; self.status=f"connecting to {net['ssid']}..."; self.draw(scr)
        r=connect(self.c,self.cut_c,self.outs,net,bb,psk,lambda s:(self.logline(s),setattr(self,'status',s),self.draw(scr)))
        if not r: self.state='idle'; self.status=f"connect to {net['ssid']} FAILED"; return
        self.conn=r; self.cur_bssid=bb; self.cur_ssid=net['ssid']
        if r[0]=='OPEN':
            self.state='connected'; self.status=f"connected to {net['ssid']} (open, no DHCP yet)"; return
        self.status="getting IP (DHCP)..."; self.draw(scr)
        lease=dhcp_lease(self.c,self.outs,r[0],bb,self.logline)
        self.lease=lease
        if lease:
            self.state='connected'
            self.status=f"resolving gateway..."; self.draw(scr)
            self.gw_mac=resolve_gw(self.c,self.outs,r[0],bb,lease['yiaddr'],lease['router'])
            ips='.'.join(map(str,lease['yiaddr']))
            self.status=f"CONNECTED {net['ssid']} — IP {ips}" + ("  (press b to route all traffic)" if self.gw_mac else "")
        else:
            self.state='connected'; self.status=f"connected {net['ssid']} but DHCP failed"

    def do_ping(self, scr):
        if not (self.lease and self.conn and self.conn[0]!='OPEN'): self.status="ping needs a WPA2 lease"; return
        self.status="pinging 8.8.8.8..."; self.draw(scr)
        got=ping_test(self.c,self.outs,self.conn[0],self.cur_bssid,self.lease['yiaddr'],self.lease['router'],self.logline)
        self.status=f"ping 8.8.8.8: {got}/6 replies — {'INTERNET OK' if got else 'no reply'}"

    def toggle_bridge(self, scr):
        if self.bridge_on: self.stop_bridge(); return
        if not self.is_root:
            self.status="full routing needs root — run:  sudo python3 wifi_tui.py"; return
        if not (self.lease and self.conn and self.conn[0]!='OPEN' and self.gw_mac):
            self.status="connect to a WPA2 network first (need lease + gateway)"; return
        self.start_bridge(scr)

    def start_bridge(self, scr):
        self.status="starting full-traffic bridge..."; self.draw(scr)
        tk=self.conn[0]; bssid=self.cur_bssid
        our_ip=self.lease['yiaddr']; gw_ip=self.lease['router']
        ips='.'.join(map(str,our_ip)); gws='.'.join(map(str,gw_ip)); gwm=self.gw_mac
        self.utun,self.utun_name=utun_open(); name=self.utun_name
        _sh(f"ifconfig {name} inet {ips} {gws} up"); _sh(f"ifconfig {name} mtu 1400")
        _sh(f"route -n add -net 0.0.0.0/1 -interface {name}")
        _sh(f"route -n add -net 128.0.0.0/1 -interface {name}")
        dns_up(name, ips, gws)
        self.bstats={'tx':0,'rx':0,'dup':0}
        self.brun=threading.Event(); self.brun.set()
        utun=self.utun; st=self.bstats; run=self.brun
        arp=bytes([0,1,8,0,6,4,0,1])+OUR_MAC+our_ip+b'\x00'*6+gw_ip
        pnl=threading.Lock(); last_pn={}
        def esend(a3,eth,payload):
            hdr=bytes([0x08,0x41])+b'\x00\x00'+bssid+OUR_MAC+a3+b'\x00\x00'
            with pnl:
                p=PN[0]; PN[0]+=1                 # continue the shared PN (no replay-reject)
                e=conn.ccmp_encrypt(tk,hdr,bytes([0xaa,0xaa,0x03,0,0,0])+eth+payload,p)
                try: conn.send_data(self.c,self.outs,hdr+e,sec=0)
                except Exception: pass
        def txt():
            utun.setblocking(False); ka=0
            while run.is_set():
                rl,_,_=select.select([utun],[],[],0.3)
                if rl:
                    for _ in range(256):
                        try: pkt=utun.recv(2048)
                        except (BlockingIOError,OSError): break
                        if len(pkt)>4: esend(gwm,b'\x08\x00',pkt[4:]); st['tx']+=1
                if time.time()-ka>3: esend(b'\xff'*6,b'\x08\x06',arp); ka=time.time()
        def rxt():
            while run.is_set():
                try: d=self.c.dev.read(0x81,32768,timeout=300)
                except Exception: continue
                if not d: continue
                buf=bytes(d); i=0; n=len(buf)
                while i+24<=n:
                    w0=struct.unpack_from('<I',buf,i)[0]; pl=w0&0x3FFF; dv=(w0>>16)&0xF; s=(w0>>24)&0x3
                    if pl==0 or pl>8192: break
                    w2=struct.unpack_from('<I',buf,i+8)[0]; h=i+24+dv*8+s
                    if not((w2>>28)&1) and h+30<=n and h+pl<=n:
                        fc=buf[h]; typ=(fc>>2)&3; prot=(buf[h+1]>>6)&1
                        a1=buf[h+4:h+10]; a2=buf[h+10:h+16]
                        if typ==2 and a2==bssid and prot:
                            hl=26 if (fc&0xF0)==0x80 else 24
                            cc=buf[h+hl:h+hl+8]; kid=(cc[3]>>6)&3
                            pnv=(cc[7]<<40)|(cc[6]<<32)|(cc[5]<<24)|(cc[4]<<16)|(cc[1]<<8)|cc[0]
                            if pnv<=last_pn.get(kid,-1): st['dup']+=1
                            else:
                                last_pn[kid]=pnv; pt=buf[h+hl+8:h+pl]
                                if pt[:6]==b'\xaa\xaa\x03\x00\x00\x00':
                                    et=pt[6:8]
                                    if et==b'\x08\x06' and pt[14:16]==b'\x00\x01' and pt[16+16:16+20]==bytes(our_ip):
                                        sm=pt[16:16+6]; si=pt[16+6:16+10]
                                        esend(sm,b'\x08\x06',bytes([0,1,8,0,6,4,0,2])+OUR_MAC+our_ip+sm+si)
                                    elif et==b'\x08\x00' and (a1==OUR_MAC or a1==b'\xff'*6):
                                        try: utun.send(struct.pack('>I',socket.AF_INET)+pt[8:]); st['rx']+=1
                                        except Exception: pass
                    adv=(24+dv*8+s+pl+7)&~7
                    if adv<=0: break
                    i+=adv
        threading.Thread(target=txt,daemon=True).start()
        threading.Thread(target=rxt,daemon=True).start()
        self.bridge_on=True
        self.status=f"BRIDGE ON — all traffic via {self.cur_ssid} through the Alfa"

    def stop_bridge(self):
        if not self.bridge_on: return
        self.status="stopping bridge, restoring routing..."
        if self.brun: self.brun.clear()
        time.sleep(0.4); name=self.utun_name
        _sh(f"route -n delete -net 0.0.0.0/1 -interface {name}")
        _sh(f"route -n delete -net 128.0.0.0/1 -interface {name}")
        dns_down()
        try: self.utun.close()
        except Exception: pass
        self.bridge_on=False; self.utun=None
        self.status=f"bridge off — routing restored (connected {self.cur_ssid})"

    def _put(self, scr, y, x, s, w, attr=0):
        try: scr.addnstr(y, x, s, max(0,w), attr)
        except curses.error: pass

    def draw(self, scr):
        scr.erase(); H,W=scr.getmaxyx()
        title="  alfawifi — RTL8812AU userspace driver (Apple Silicon, no kext)"
        self._put(scr,0,0,title.ljust(W-1),W-1,curses.A_REVERSE)
        self._put(scr,1,0,f"  status: {self.status}",W-1,curses.A_BOLD)
        if self.bridge_on:
            s=self.bstats
            self._put(scr,2,0,f"  >>> ROUTING ALL MAC TRAFFIC THROUGH THE ALFA <<<   tx={s['tx']} rx={s['rx']} dedup'd={s['dup']}",W-1,curses.A_REVERSE)
        else:
            self._put(scr,2,0,"  "+"-"*(W-4),W-1)
        self._put(scr,3,2,f"{'SSID':<28}{'BAND':<6}{'CH':<5}{'SEC':<6}{'BSSID'}",W-3,curses.A_UNDERLINE)
        row=4
        for i,b in enumerate(self.order):
            if row>=H-10: break
            n=self.nets[b]
            line=f"{n['ssid'][:27]:<28}{n['band']+'G':<6}{str(n['ch']):<5}{'WPA2' if n['secured'] else 'open':<6}{b}"
            mark="> " if i==self.sel else "  "
            attr=curses.A_REVERSE if i==self.sel else 0
            if b==self.cur_bssid: attr|=curses.A_BOLD
            self._put(scr,row,0,mark+line,W-1,attr); row+=1
        ly=H-9; self._put(scr,ly,0,"  "+"-"*(W-4),W-1)
        self._put(scr,ly+1,2,"log:",W-3,curses.A_DIM)
        for j,l in enumerate(self.log[-6:]): self._put(scr,ly+2+j,4,l,W-5,curses.A_DIM)
        bkey = "b=STOP routing" if self.bridge_on else ("b=route ALL traffic" if self.is_root else "b=route(needs sudo)")
        foot=f" s=scan  Enter=connect  {bkey}  p=ping  d=disconnect  q=quit "
        self._put(scr,H-1,0,foot.ljust(W-1),W-1,curses.A_REVERSE)
        scr.refresh()

    def prompt(self, scr, msg):
        H,W=scr.getmaxyx(); curses.echo(); curses.curs_set(1)
        scr.addnstr(H-2,0,("  "+msg).ljust(W),W-1,curses.A_BOLD); scr.refresh()
        s=scr.getstr(H-2,len(msg)+4,64).decode(errors='ignore')
        curses.noecho(); curses.curs_set(0); return s

    def run(self, scr):
        curses.curs_set(0); scr.keypad(True); scr.timeout(700)   # timeout => live stats refresh
        self.status="initializing RTL8812AU (power/firmware/radio)... ~30s"; self.draw(scr)
        try: self.init_adapter()
        except Exception as e: self.status=f"init failed: {e}"; self.draw(scr); scr.timeout(-1); scr.getch(); return
        self.draw(scr)
        while True:
            k=scr.getch()
            if k==-1:                       # timeout tick: just refresh (bridge stats)
                if self.bridge_on: self.draw(scr)
                continue
            if k in (ord('q'),27):
                if self.bridge_on: self.stop_bridge()
                break
            elif k==ord('b'): self.toggle_bridge(scr)
            elif self.bridge_on and k in (ord('s'),curses.KEY_ENTER,10,13):
                self.status="stop routing (b) before scanning/connecting"
            elif k==ord('s'): self.do_scan(scr)
            elif k in (curses.KEY_UP,ord('k')): self.sel=max(0,self.sel-1)
            elif k in (curses.KEY_DOWN,ord('j')): self.sel=min(len(self.order)-1,self.sel+1) if self.order else 0
            elif k in (curses.KEY_ENTER,10,13):
                if self.order:
                    net=self.nets[self.order[self.sel]]
                    psk=self.prompt(scr,f"password for {net['ssid']} (blank if open): ") if net['secured'] else ''
                    self.do_connect(scr,psk)
            elif k==ord('d'):
                if self.bridge_on: self.stop_bridge()
                self.conn=None; self.lease=None; self.cur_bssid=None; self.gw_mac=None
                self.state='idle'; self.status="disconnected"
            elif k==ord('p'):
                if not self.bridge_on: self.do_ping(scr)
            self.draw(scr)

def _main(scr):
    app=App()
    try: app.run(scr)
    finally:
        if app.bridge_on:
            try: app.stop_bridge()
            except Exception: pass

if __name__=="__main__":
    curses.wrapper(_main)
