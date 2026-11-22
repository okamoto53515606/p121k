import { NextResponse, type NextRequest } from "next/server";

/**
 * すべてのリクエストに Basic 認証を要求し、localhost 以外の Host を拒否する。
 *
 * why 認証: 画面から CDK を動かせる = AWS の権限を操作できる。ネットワークに
 *            出さない前提でも、同一 PC の他プロセスやブラウザ拡張から守るために必要。
 * why Host 検査: DNS リバインディングで外部ページから localhost を叩かれる経路を塞ぐ。
 *            Host が localhost/127.0.0.1 以外なら 403。
 */
const LOCAL_HOST = /^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/i;

function safeEqual(a: string, b: string): boolean {
  const ab = new TextEncoder().encode(a);
  const bb = new TextEncoder().encode(b);
  let diff = ab.length ^ bb.length;
  const n = Math.max(ab.length, bb.length);
  for (let i = 0; i < n; i++) diff |= (ab[i] ?? 0) ^ (bb[i] ?? 0);
  return diff === 0;
}

export function middleware(request: NextRequest) {
  const host = request.headers.get("host") ?? "";
  if (!LOCAL_HOST.test(host)) {
    return new NextResponse("forbidden host", { status: 403 });
  }

  const user = process.env.P121K_SETUP_USER ?? "";
  const pass = process.env.P121K_SETUP_PASS ?? "";
  if (!user || !pass) {
    return new NextResponse(
      "P121K_SETUP_USER / P121K_SETUP_PASS が未設定です。setup/start.sh から起動してください。",
      { status: 500 },
    );
  }

  const header = request.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    // Edge ランタイムには Buffer が無いため atob を使う
    let decoded = "";
    try {
      decoded = new TextDecoder().decode(Uint8Array.from(atob(header.slice(6)), (c) => c.charCodeAt(0)));
    } catch {
      decoded = "";
    }
    const idx = decoded.indexOf(":");
    if (idx > 0 && safeEqual(decoded.slice(0, idx), user) && safeEqual(decoded.slice(idx + 1), pass)) {
      return NextResponse.next();
    }
  }

  return new NextResponse("認証が必要です", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="P121K setup", charset="UTF-8"' },
  });
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
