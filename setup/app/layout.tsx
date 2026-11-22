import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "P121K セットアップ",
  description: "Personalized One-to-One Karte の構築と運用（ローカル専用）",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja">
      <body>
        <header className="site-header">
          <div>
            <h1>P121K セットアップ</h1>
            <p className="muted small">Personalized One-to-One Karte / ap-northeast-1</p>
          </div>
          <span className="badge">localhost 専用</span>
        </header>
        {children}
      </body>
    </html>
  );
}
