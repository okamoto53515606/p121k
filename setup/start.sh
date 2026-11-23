#!/usr/bin/env bash
# P121K セットアップ画面の起動スクリプト（localhost:3011）。
#
# why このスクリプト経由: 画面は CDK を実行できるため、認証なしで起動させない。
#   パスワードは環境変数が無ければ生成し、0600 のファイルに保存して表示する。
#   同じ PC の別プロセスからは読めるが、ネットワークからは到達できない（127.0.0.1 バインド）。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${P121K_SETUP_PORT:-3011}"
mkdir -p .state
chmod 700 .state

# why: 前回のプロセスが残っていると EADDRINUSE で起動できない。
#      自分たちの Next サーバ（next-server / next start）だけを止めて再利用する。
port_pids() {
  ss -ltnp "sport = :${PORT}" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u || true
}
pids="$(port_pids || true)"
if [[ -n "$pids" ]]; then
  ours=""
  for pid in $pids; do
    cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    case "$cmd" in
      *next-server*|*"next start"*|*"next dev"*) ours="$ours $pid" ;;
    esac
  done
  if [[ -n "$ours" ]]; then
    echo "前回のセットアップ画面を停止します（PID:$ours）"
    kill $ours 2>/dev/null || true
    for _ in 1 2 3 4 5; do [[ -z "$(port_pids)" ]] && break; sleep 1; done
  fi
  if [[ -n "$(port_pids)" ]]; then
    echo "ポート ${PORT} は他のアプリが使用中です。3021 で起動します。"
    PORT=3021
  fi
fi

if [[ -z "${P121K_SETUP_USER:-}" ]]; then
  export P121K_SETUP_USER="admin"
fi
if [[ -z "${P121K_SETUP_PASS:-}" ]]; then
  if [[ -f .state/pass ]]; then
    export P121K_SETUP_PASS="$(cat .state/pass)"
  else
    export P121K_SETUP_PASS="$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | cut -c1-16)"
    (umask 077; printf '%s' "$P121K_SETUP_PASS" > .state/pass)
  fi
fi

if [[ ! -d node_modules ]]; then
  echo "依存パッケージをインストールします（初回のみ）..."
  npm install --no-audit --no-fund
fi

# 本番ビルドがあればそれを使う（起動が速い）。無ければ開発サーバ。
if [[ -d .next ]] && [[ "${P121K_SETUP_DEV:-0}" != "1" ]]; then
  MODE="start"
else
  MODE="dev"
fi

cat <<EOF

  P121K セットアップ画面
  ---------------------------------------------
  URL:      http://127.0.0.1:${PORT}
  ユーザー: ${P121K_SETUP_USER}
  パスワード: ${P121K_SETUP_PASS}
  （.state/pass に保存。消せば次回起動時に再生成）
  ---------------------------------------------
  停止: Ctrl+C

EOF

if [[ "$MODE" == "start" ]]; then
  exec npx next start -p "$PORT" -H 127.0.0.1
else
  exec npx next dev -p "$PORT" -H 127.0.0.1
fi
