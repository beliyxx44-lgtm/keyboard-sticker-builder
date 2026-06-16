#!/bin/bash
set -e

# Цвета для вывода
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=== Установка Keycap Sticker Builder ===${NC}"

# 1. Системные пакеты
echo -e "${GREEN}[1/6] Установка системных пакетов...${NC}"
apt update
apt install -y python3 python3-venv python3-pip nginx ufw

# 2. Создание директорий и копирование файлов
echo -e "${GREEN}[2/6] Подготовка директорий...${NC}"
mkdir -p /opt/sticker_builder/static
mkdir -p /opt/sticker_builder/layouts

# Если скрипт запускается из папки с проектом, копируем файлы
if [ -f "app.py" ]; then
    cp app.py /opt/sticker_builder/
    cp requirements.txt /opt/sticker_builder/
    cp -r static/* /opt/sticker_builder/static/
    echo "Файлы проекта скопированы из текущей директории."
else
    echo -e "${YELLOW}Файлы проекта не найдены в текущей директории. Убедитесь, что app.py, requirements.txt и папка static существуют рядом со скриптом.${NC}"
    exit 1
fi

# 3. Виртуальное окружение и зависимости Python
echo -e "${GREEN}[3/6] Настройка виртуального окружения Python...${NC}"
cd /opt/sticker_builder
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

# 4. Systemd сервис
echo -e "${GREEN}[4/6] Создание systemd сервиса...${NC}"
cat > /etc/systemd/system/sticker_builder.service << 'EOF'
[Unit]
Description=Sticker Builder Flask App
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/sticker_builder
Environment="PATH=/opt/sticker_builder/venv/bin"
Environment="TMPDIR=/tmp"
Environment="HOME=/tmp"
ExecStart=/opt/sticker_builder/venv/bin/gunicorn --workers 2 --bind 0.0.0.0:5000 --worker-tmp-dir /tmp app:app
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable sticker_builder
systemctl start sticker_builder

# 5. Nginx конфигурация
echo -e "${GREEN}[5/6] Настройка Nginx...${NC}"
cat > /etc/nginx/sites-available/sticker_builder << 'EOF'
server {
    listen 80;
    server_name _;

    client_max_body_size 1024M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /static {
        alias /opt/sticker_builder/static;
    }
}
EOF

ln -sf /etc/nginx/sites-available/sticker_builder /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# 6. Брандмауэр
echo -e "${GREEN}[6/6] Настройка брандмауэра...${NC}"
ufw allow 80/tcp
ufw --force enable

# Права на папку layouts
chown -R www-data:www-data /opt/sticker_builder/layouts
chmod 775 /opt/sticker_builder/layouts

echo -e "${GREEN}=== Установка завершена! ===${NC}"
echo "Конструктор доступен по адресу http://$(hostname -I | awk '{print $1}')/"
echo "Редактор трафаретов доступен по адресу http://$(hostname -I | awk '{print $1}')/editor"
echo "Логин: admin"
echo "Пароль: sticker2025"