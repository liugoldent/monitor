# restart Telegram signal relay
cd "$HOME\OneDrive\桌面\monitor"
docker compose up -d --build telegram-signal-relay

# start(看logs)
docker compose logs -f --tail 0 telegram-signal-relay

# stop
docker compose stop telegram-signal-relay
