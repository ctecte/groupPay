#!/bin/sh

cloudflared tunnel run --token $TUNNEL_TOKEN --url http://localhost:5000 &
python3 bot.py