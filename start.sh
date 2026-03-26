#!/bin/bash
# GroupPay — start bot + tunnel in one command
# Usage: ./start.sh              (reuses existing tunnel, or starts cloudflare)
#        ./start.sh cloudflare   (force new cloudflare tunnel)
#        ./start.sh ngrok        (uses ngrok with stable domain)
#        ./start.sh ngrok-free   (uses ngrok free tier, random URL)
cd "$(dirname "$0")"

TUNNEL=${1:-auto}

# Kill existing bot (but not the tunnel unless we're starting a new one)
pkill -f 'python.*bot\.py' 2>/dev/null
sleep 1
rm -rf __pycache__

# Activate venv
source venv/bin/activate

# Build frontend
echo "Building frontend..."
cd splitwize-spark && npx vite build 2>&1 | tail -1 && cd ..

# Check for existing tunnel
EXISTING_URL=""
if [ "$TUNNEL" = "auto" ]; then
    # Check if cloudflared is already running
    if pgrep -f cloudflared > /dev/null 2>&1; then
        EXISTING_URL=$(grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' cloudflared.log 2>/dev/null | head -1)
        if [ -n "$EXISTING_URL" ]; then
            echo "Reusing existing Cloudflare tunnel: $EXISTING_URL"
            TUNNEL="reuse"
        fi
    fi
    # No existing tunnel — start cloudflare
    [ "$TUNNEL" = "auto" ] && TUNNEL="cloudflare"
fi

if [ "$TUNNEL" = "reuse" ]; then
    WEBAPP_URL="$EXISTING_URL"
elif [ "$TUNNEL" = "cloudflare" ]; then
    # Kill old tunnel if starting fresh
    pkill -f cloudflared 2>/dev/null
    sleep 1
    echo "Starting Cloudflare Tunnel..."
    nohup cloudflared tunnel --url http://localhost:5000 > cloudflared.log 2>&1 &
    for i in $(seq 1 15); do
        WEBAPP_URL=$(grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' cloudflared.log | head -1)
        [ -n "$WEBAPP_URL" ] && break
        sleep 2
    done
    if [ -z "$WEBAPP_URL" ]; then
        echo "Cloudflare tunnel failed to start. Check cloudflared.log"
        exit 1
    fi
elif [ "$TUNNEL" = "ngrok" ]; then
    pkill -f ngrok 2>/dev/null
    sleep 1
    echo "Starting ngrok (stable domain)..."
    NGROK_DOMAIN="${NGROK_DOMAIN:-subfossorial-ritualistically-christene.ngrok-free.dev}"
    nohup ngrok http 5000 --domain="$NGROK_DOMAIN" --log=stdout > ngrok.log 2>&1 &
    sleep 3
    WEBAPP_URL="https://$NGROK_DOMAIN"
elif [ "$TUNNEL" = "ngrok-free" ]; then
    pkill -f ngrok 2>/dev/null
    sleep 1
    echo "Starting ngrok (free tier)..."
    nohup ngrok http 5000 --log=stdout > ngrok.log 2>&1 &
    sleep 3
    WEBAPP_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)["tunnels"][0]["public_url"])' 2>/dev/null)
    if [ -z "$WEBAPP_URL" ]; then
        echo "ngrok failed to start. Check ngrok.log"
        exit 1
    fi
fi

export WEBAPP_URL
echo "Tunnel: $WEBAPP_URL"

# Start bot
WEBAPP_URL="$WEBAPP_URL" nohup python -u bot.py > bot.log 2>&1 &
sleep 3

# Verify with HTTP check
HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:5000/ 2>/dev/null)
if [ "$HTTP_CODE" != "200" ]; then
    echo "Bot failed to start. Check bot.log"
    tail -5 bot.log
    exit 1
fi

BOT_PID=$(pgrep -f 'python.*bot\.py' | head -1)
echo ""
echo "Bot running (PID $BOT_PID)"
echo "$WEBAPP_URL"
echo ""
echo "Logs:  tail -f bot.log"
echo "Stop:  kill $BOT_PID"
