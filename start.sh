#!/bin/bash
# GroupPay — start bot + tunnel in one command
# Usage: ./start.sh              (uses cloudflare tunnel)
#        ./start.sh ngrok        (uses ngrok with stable domain)
#        ./start.sh ngrok-free   (uses ngrok free tier, random URL)
cd "$(dirname "$0")"

TUNNEL=${1:-cloudflare}

# Kill existing processes
kill $(ps aux | grep bot.py | grep -v grep | grep -v bash | awk '{print $2}') 2>/dev/null
kill $(ps aux | grep ngrok | grep -v grep | awk '{print $2}') 2>/dev/null
kill $(ps aux | grep cloudflared | grep -v grep | awk '{print $2}') 2>/dev/null
sleep 1

# Build frontend
source venv/bin/activate
cd splitwize-spark && npx vite build 2>&1 | tail -1 && cd ..

# Start tunnel
if [ "$TUNNEL" = "cloudflare" ]; then
    echo "🌐 Starting Cloudflare Tunnel..."
    nohup cloudflared tunnel --url http://localhost:5000 > cloudflared.log 2>&1 &
    for i in $(seq 1 15); do
        WEBAPP_URL=$(grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' cloudflared.log | head -1)
        [ -n "$WEBAPP_URL" ] && break
        sleep 2
    done
    if [ -z "$WEBAPP_URL" ]; then
        echo "❌ Cloudflare tunnel failed to start. Check cloudflared.log"
        exit 1
    fi
elif [ "$TUNNEL" = "ngrok" ]; then
    echo "🌐 Starting ngrok (stable domain)..."
    NGROK_DOMAIN="${NGROK_DOMAIN:-subfossorial-ritualistically-christene.ngrok-free.dev}"
    nohup ngrok http 5000 --domain="$NGROK_DOMAIN" --log=stdout > ngrok.log 2>&1 &
    sleep 3
    WEBAPP_URL="https://$NGROK_DOMAIN"
elif [ "$TUNNEL" = "ngrok-free" ]; then
    echo "🌐 Starting ngrok (free tier)..."
    nohup ngrok http 5000 --log=stdout > ngrok.log 2>&1 &
    sleep 3
    WEBAPP_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)["tunnels"][0]["public_url"])' 2>/dev/null)
    if [ -z "$WEBAPP_URL" ]; then
        echo "❌ ngrok failed to start. Check ngrok.log"
        exit 1
    fi
fi

export WEBAPP_URL
echo "🌐 Tunnel URL: $WEBAPP_URL"

# Start bot with WEBAPP_URL passed explicitly
WEBAPP_URL="$WEBAPP_URL" nohup python -u bot.py > bot.log 2>&1 &
sleep 2

# Verify
BOT_PID=$(pgrep -f 'python bot.py')
if [ -z "$BOT_PID" ]; then
    echo "❌ Bot failed to start. Check bot.log"
    exit 1
fi

echo ""
echo "✅ Bot PID: $BOT_PID"
echo "🌐 $WEBAPP_URL"
echo ""
echo "To check status:  tail -f bot.log"
echo "To stop:          kill $BOT_PID"
