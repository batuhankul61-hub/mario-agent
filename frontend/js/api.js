// Calls to the Mario backend, which serves this page too (same origin). The
// Gemini API key never reaches the browser; the backend hands out a
// short-lived, single-use token instead, and runs every command itself.

const CODE_KEY = "mario.accessCode";

export const accessCode = {
  get: () => safe(() => localStorage.getItem(CODE_KEY)) ?? "",
  set: (v) => safe(() => localStorage.setItem(CODE_KEY, v)),
};

function safe(fn) {
  try {
    return fn();
  } catch {
    return null;
  }
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: { "Content-Type": "application/json", "X-Access-Code": accessCode.get() },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Sunucuya ulaşılamıyor. Bilgisayardaki Mario açık mı?");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, typeof data.detail === "string" ? data.detail : "İstek başarısız.");
  return data;
}

export const createToken = () => request("POST", "/api/token", {});
export const runTool = (name, args, utterances = []) => request("POST", "/api/tool", { name, args, utterances });
export const answerConfirmation = (id, approved) => request("POST", `/api/confirm/${encodeURIComponent(id)}`, { approved });
export const jobStatus = (id) => request("GET", `/api/jobs/${encodeURIComponent(id)}`);

/** Screenshots need the access-code header, which <img src> can't send. */
export async function imageUrl(path) {
  const res = await fetch(path, { headers: { "X-Access-Code": accessCode.get() } });
  if (!res.ok) throw new ApiError(res.status, "Görüntü alınamadı.");
  return URL.createObjectURL(await res.blob());
}
