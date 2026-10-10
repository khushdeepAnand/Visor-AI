import { api } from "@/lib/api";

export async function registerPasskey() {
  if (!window.PublicKeyCredential?.parseCreationOptionsFromJSON) throw new Error("This browser does not support passkeys. Use a current browser on HTTPS or localhost.");
  const options = await api<PublicKeyCredentialCreationOptionsJSON>("/api/v1/auth/webauthn/register/options", { method: "POST" });
  const credential = await navigator.credentials.create({ publicKey: PublicKeyCredential.parseCreationOptionsFromJSON(options) }) as PublicKeyCredential | null;
  if (!credential) throw new Error("Passkey creation was cancelled.");
  return api("/api/v1/auth/webauthn/register/verify", { method: "POST", body: JSON.stringify({ challenge: options.challenge, response: credential.toJSON(), label: "Passkey" }) });
}

export async function verifyPasskey() {
  if (!window.PublicKeyCredential?.parseRequestOptionsFromJSON) throw new Error("This browser does not support passkeys. Use your authenticator or recovery code.");
  const options = await api<PublicKeyCredentialRequestOptionsJSON>("/api/v1/auth/webauthn/mfa/options", { method: "POST" });
  const credential = await navigator.credentials.get({ publicKey: PublicKeyCredential.parseRequestOptionsFromJSON(options) }) as PublicKeyCredential | null;
  if (!credential) throw new Error("Passkey verification was cancelled.");
  return api<{ next?: string }>("/api/v1/auth/webauthn/mfa/verify", { method: "POST", body: JSON.stringify({ challenge: options.challenge, response: credential.toJSON() }) });
}
