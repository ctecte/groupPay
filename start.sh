#!/bin/bash
# GroupPay — start bot + tunnel in one command
# Usage: ./start.sh              (named tunnel → bot.grouppay.uk)
#        ./start.sh cloudflare   (quick tunnel → random trycloudflare.com)
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

# Named Cloudflare Tunnel config
CF_TUNNEL_NAME="grouppay"
CF_TUNNEL_HOSTNAME="bot.grouppay.uk"
CF_TUNNEL_ID="45ae2fc4-abff-48c9-a21d-c79fda73e46a"
CF_CREDS="$HOME/.cloudflared/${CF_TUNNEL_ID}.json"

# Check for existing tunnel
EXISTING_URL=""
if [ "$TUNNEL" = "auto" ]; then
    # Default to named tunnel
    TUNNEL="named"
fi

if [ "$TUNNEL" = "named" ]; then
    # Kill old tunnel if running
    pkill -f cloudflared 2>/dev/null
    sleep 1
    echo "Starting named Cloudflare Tunnel ($CF_TUNNEL_HOSTNAME)..."
    nohup cloudflared tunnel --no-autoupdate \
        --origincert "$HOME/.cloudflared/cert.pem" \
        --credentials-file "$CF_CREDS" \
        --url http://localhost:5000 \
        run "$CF_TUNNEL_NAME" > cloudflared.log 2>&1 &
    sleep 3
    WEBAPP_URL="https://$CF_TUNNEL_HOSTNAME"
elif [ "$TUNNEL" = "cloudflare" ]; then
    # Quick tunnel (ephemeral trycloudflare.com URL)
    pkill -f cloudflared 2>/dev/null
    sleep 1
    echo "Starting Cloudflare quick tunnel..."
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

# Load env vars (API keys etc.)
[ -f .env ] && export $(grep -v '^#' .env | xargs)

# Start bot
WEBAPP_URL="$WEBAPP_URL" nohup ./venv/bin/python -u bot.py > bot.log 2>&1 &
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
