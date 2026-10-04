"use client";
import { Suspense, FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { safeNextPath, useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Eye, EyeOff } from "lucide-react";
import { OAuthButtons } from "@/components/OAuthButtons";

function RegisterContent() {
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [ready, setReady] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [created, setCreated] = useState(false);
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNextPath(params.get("next"));
  const { status, refresh } = useAuth();
  useEffect(() => setReady(true), []);

  useEffect(() => {
    if (status === "authenticated" && !created) router.replace(next);
  }, [status, next, created, router]);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    setPending(true);
    const f = new FormData(e.currentTarget);
    try {
      await api("/api/v1/auth/register", {
        method: "POST",
        body: JSON.stringify({ name: f.get("name"), email: f.get("email"), password: f.get("password"), date_of_birth: f.get("date_of_birth") }),
      });
      setCreated(true);
      await refresh();
      router.replace(`/onboarding?next=${encodeURIComponent(next)}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center p-4 terminal-grid">
      <form method="post" onSubmit={submit} className="w-full max-w-md rounded-xl border border-slate-800 bg-terminal-900 p-6">
        <div className="mb-6">
          <div className="text-xs uppercase tracking-[.2em] text-accent">StockPilot AI</div>
          <h1 className="mt-2 text-2xl font-semibold">Create your workspace</h1>
          <p className="mt-2 text-sm leading-5 text-slate-500">Save research ranges, alerts, portfolios, and paper simulations without enabling broker execution.</p>
        </div>
        <OAuthButtons nextPath={next} />
        <div className="my-5 flex items-center gap-3 text-[10px] uppercase tracking-wider text-slate-600"><span className="h-px flex-1 bg-slate-800"/>or register with password<span className="h-px flex-1 bg-slate-800"/></div>
        <div className="space-y-3">
          <label htmlFor="register-name" className="block text-xs text-slate-300">Name</label>
          <Input id="register-name" name="name" placeholder="Your name" autoComplete="name" required />
          <label htmlFor="register-email" className="block text-xs text-slate-300">Email address</label>
          <Input id="register-email" name="email" type="email" placeholder="email@example.com" autoComplete="email" aria-describedby={error ? "register-error" : undefined} required />
          <label htmlFor="register-password" className="block text-xs text-slate-300">Password</label>
          <div className="relative"><Input id="register-password" name="password" type={showPassword ? "text" : "password"} placeholder="At least 8 characters" autoComplete="new-password" aria-describedby={error ? "register-error" : undefined} required /><button type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? "Hide password" : "Show password"} className="absolute inset-y-0 right-0 grid w-11 place-items-center text-slate-500 hover:text-slate-200">{showPassword ? <EyeOff aria-hidden="true" size={16}/> : <Eye aria-hidden="true" size={16}/>}</button></div>
          <label htmlFor="register-dob" className="block text-xs text-slate-300">Date of birth</label>
          <Input id="register-dob" name="date_of_birth" type="date" placeholder="YYYY-MM-DD" aria-describedby={error ? "register-error" : undefined} required />
          <p className="text-[10px] leading-4 text-slate-500">You must be at least 15 years old on the date you register.</p>
          {error && <p id="register-error" role="alert" className="rounded border border-loss/30 bg-loss/5 p-3 text-xs leading-5 text-loss">{error}</p>}
          <Button className="w-full" type="submit" disabled={!ready || pending}>{pending ? "Creating…" : "Create account"}</Button>
        </div>
        <p className="mt-4 text-xs text-slate-500">Already registered? <Link className="text-accent" href={`/login?next=${encodeURIComponent(next)}`}>Sign in</Link></p>
      </form>
    </main>
  );
}

export default function Register() {
  return (
    <Suspense>
      <RegisterContent />
    </Suspense>
  );
}
