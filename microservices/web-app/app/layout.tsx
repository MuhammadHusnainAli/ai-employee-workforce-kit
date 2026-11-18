import type { Metadata } from "next";
import Link from "next/link";
import { Atkinson_Hyperlegible_Next, Bricolage_Grotesque } from "next/font/google";
import { ActorPicker } from "@/components/bits";
import { NavLinks } from "@/components/nav";
import "./globals.css";

const display = Bricolage_Grotesque({ subsets: ["latin"], variable: "--font-display" });
const text = Atkinson_Hyperlegible_Next({ subsets: ["latin"], variable: "--font-text" });

export const metadata: Metadata = {
    title: { default: "Workforce", template: "%s | Workforce" },
    description:
        "Supervise your AI employees: their tasks, the decisions they wait on, and what they know.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
    return (
        <html lang="en" className={`${display.variable} ${text.variable}`}>
            <body>
                <a className="skip" href="#main">
                    Skip to content
                </a>
                <div className="shell">
                    <aside className="rail">
                        <Link href="/tasks" className="mark">
                            Workforce
                        </Link>
                        <NavLinks />
                        <ActorPicker />
                    </aside>
                    <main id="main" className="main">
                        {children}
                    </main>
                </div>
            </body>
        </html>
    );
}
