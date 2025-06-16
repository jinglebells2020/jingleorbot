import Chat from '../components/Chat';

export default function MyApp({ Component, pageProps }) {
  return (
    <div>
      <Chat />
      <Component {...pageProps} />
    </div>
  );
}
