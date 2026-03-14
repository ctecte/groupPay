#!/bin/bash
# GroupPay — start bot + ngrok in one command
# Usage: ./start.sh
cd "$(dirname "$0")"
kill $(ps aux | grep bot.py | grep -v grep | grep -v bash | awk '{print $2}') 2>/dev/null
kill $(ps aux | grep ngrok | grep -v grep | awk '{print $2}') 2>/dev/null
sleep 1
source venv/bin/activate
cd splitwize-spark && npx vite build 2>&1 | tail -1 && cd ..
nohup ngrok http 5000 --domain=subfossorial-ritualistically-christene.ngrok-free.dev --log=stdout > ngrok.log 2>&1 &
nohup python bot.py > bot.log 2>&1 &
sleep 2
echo "✅ Bot PID: $(pgrep -f 'python bot.py')"
echo "✅ ngrok PID: $(pgrep -f ngrok)"
echo "🌐 $(curl -s http://localhost:4040/api/tunnels 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)["tunnels"][0]["public_url"])' 2>/dev/null || echo 'ngrok starting...')"
