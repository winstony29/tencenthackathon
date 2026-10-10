import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "DCTA ? Banking, in your words",
  description: "A voice banking hackathon prototype. Review the details before taking action.",
};
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="app-shell">
          <header className="app-header">
            <Link className="brand" href="/" aria-label="DCTA home">
              <span className="brand-mark" aria-hidden="true">
                ✳
              </span>
              <strong>DCTA</strong>
              <span className="brand-divider" />
              <span className="brand-subtitle">Banking, in your words.</span>
            </Link>
            <span className="prototype-badge">Hackathon prototype</span>
            <span className="profile" aria-label="Demo user">
              D
            </span>
          </header>
          <main>
            <div className="workspace-heading">
              <div>
                <p className="eyebrow">YOUR PERSONAL BANKING WORKSPACE</p>
                <h1>
                  A few words.
                  <br />
                  <span>A page made for you.</span>
                </h1>
                <p className="intro">
                  Tell us what you need. We’ll bring the right task and useful details together, so you can
                  check before you act.
                </p>
              </div>
              <div className="workflow-note">
                <span aria-hidden="true">↗</span>
                <p>
                  Your words
                  <br />
                  <strong>Your task. Your approval.</strong>
                </p>
              </div>
            </div>
            {children}
            <footer className="app-footer">
              <span>DCTA · Tencent Cloud Hackathon 2026</span>
              <span>Draft first. You approve.</span>
            </footer>
          </main>
        </div>
      </body>
    </html>
  );
}
