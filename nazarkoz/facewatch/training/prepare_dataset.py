#!/usr/bin/env python3
"""Convert the Granada OD-WeaponDetection VOC sets to YOLO format.

Input (sparse clone of github.com/ari-dasci/OD-WeaponDetection, CC BY-SA 4.0):
    Knife_detection/{Images,annotations}    — 'knife' class
    Pistol detection/{Weapons,xmls}         — 'pistol' class

Output: <out>/images/{train,val}/  <out>/labels/{train,val}/  <out>/weapons.yaml
Images are symlinked, not copied. 90/10 split, seeded.

Usage: prepare_dataset.py <repo_dir> <out_dir>
"""

import random
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

CLASSES = {"knife": 0, "pistol": 1}
SETS = [  # (images subdir, xml subdir)
    ("Knife_detection/Images", "Knife_detection/annotations"),
    ("Pistol detection/Weapons", "Pistol detection/xmls"),
]


def voc_to_yolo(xml_path):
    """Returns (lines, ok). Skips files with no known-class boxes."""
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError:
        return [], False
    size = root.find("size")
    w = float(size.findtext("width") or 0)
    h = float(size.findtext("height") or 0)
    if w <= 0 or h <= 0:
        return [], False
    lines = []
    for obj in root.iter("object"):
        name = (obj.findtext("name") or "").strip().lower()
        if name not in CLASSES:
            continue
        bb = obj.find("bndbox")
        x0 = float(bb.findtext("xmin")); y0 = float(bb.findtext("ymin"))
        x1 = float(bb.findtext("xmax")); y1 = float(bb.findtext("ymax"))
        x0, x1 = sorted((max(0, x0), min(w, x1)))
        y0, y1 = sorted((max(0, y0), min(h, y1)))
        bw, bh = x1 - x0, y1 - y0
        if bw < 2 or bh < 2:
            continue
        lines.append("%d %.6f %.6f %.6f %.6f"
                     % (CLASSES[name], (x0 + bw / 2) / w, (y0 + bh / 2) / h,
                        bw / w, bh / h))
    return lines, bool(lines)


def main():
    repo, out = Path(sys.argv[1]), Path(sys.argv[2])
    pairs = []  # (img_path, label_lines)
    skipped = 0
    for img_dir, xml_dir in SETS:
        images = {p.stem: p for p in (repo / img_dir).iterdir()
                  if p.suffix.lower() in (".jpg", ".jpeg", ".png")}
        for xml_path in sorted((repo / xml_dir).glob("*.xml")):
            img = images.get(xml_path.stem)
            if img is None:
                skipped += 1
                continue
            lines, ok = voc_to_yolo(xml_path)
            if not ok:
                skipped += 1
                continue
            pairs.append((img, lines))

    random.Random(42).shuffle(pairs)
    n_val = max(1, len(pairs) // 10)
    splits = {"val": pairs[:n_val], "train": pairs[n_val:]}
    for split, items in splits.items():
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        for i, (img, lines) in enumerate(items):
            stem = "%s_%05d" % (split, i)
            link = out / "images" / split / (stem + img.suffix.lower())
            if not link.exists():
                link.symlink_to(img.resolve())
            (out / "labels" / split / (stem + ".txt")).write_text(
                "\n".join(lines) + "\n")

    names = [n for n, _ in sorted(CLASSES.items(), key=lambda kv: kv[1])]
    (out / "weapons.yaml").write_text(
        "path: %s\ntrain: images/train\nval: images/val\nnames:\n%s\n"
        % (out.resolve(), "".join("  %d: %s\n" % (i, n)
                                  for i, n in enumerate(names))))
    print("train=%d val=%d skipped=%d -> %s"
          % (len(splits["train"]), len(splits["val"]), skipped,
             out / "weapons.yaml"))


if __name__ == "__main__":
    main()
