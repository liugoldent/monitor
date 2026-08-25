# restart
cd "$HOME\OneDrive\桌面\monitor"
docker compose --profile windows up -d --build h3-ef-012-strategy

# start(看logs)
docker compose logs -f --tail 0 h3-ef-012-strategy

# stop
docker compose stop h3-ef-012-strategy
