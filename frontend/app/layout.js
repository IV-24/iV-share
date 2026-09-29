import { Space_Grotesk } from "next/font/google";
import "./globals.css";

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

export const metadata = {
  title: "agent-IV",
  description: "Digital Executive Assistant",
  // No service worker/offline support -- this is a live connection to
  // your own backend, there's nothing meaningful to cache. The manifest
  // and apple-web-app tags are only for a nicer "Add to Home Screen"
  // icon/launch experience on the phone this is meant to run on.
  manifest: "/manifest.json",
  icons: {
    apple: "/apple-touch-icon.png",
  },
  appleWebApp: {
    capable: true,
    title: "iV",
    statusBarStyle: "black-translucent",
  },
};

export const viewport = {
  themeColor: "#0b1310",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={spaceGrotesk.variable}>
      <body>{children}</body>
    </html>
  );
}
