import type { Metadata } from "next";
import { headers } from "next/headers";
import { connection } from "next/server";
import { Geist, Geist_Mono } from "next/font/google";
import { THEME_STORAGE_KEY } from "./lib/constants.js";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  preload: false,
});

export const metadata: Metadata = {
  title: "AeroQuery — Aviation Regulation RAG",
  description: "Ask questions about Canadian aviation regulations with AI-powered retrieval",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  await connection();
  const nonce = (await headers()).get("x-nonce") ?? undefined;

  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        <script id="turnstile-script" nonce={nonce} src="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit" async defer />
        <script type="module" defer nonce={nonce} src="https://static.cloudflareinsights.com/beacon.min.js" data-cf-beacon='{"token": "11d46b2f74044f35b465eebcdb22ac4c"}' />
        <script nonce={nonce} dangerouslySetInnerHTML={{ __html: `
          (function() {
            var theme;
            try {
              theme = localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});
              if (theme !== null && theme !== 'light' && theme !== 'dark') {
                theme = 'dark';
                localStorage.setItem(${JSON.stringify(THEME_STORAGE_KEY)}, theme);
              }
            } catch (error) {
              console.error('Could not restore theme preference', error);
            }
            if (theme !== 'light') {
              document.documentElement.classList.add('dark');
            }
          })();
        `}} />
      </head>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
