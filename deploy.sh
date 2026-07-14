#!/bin/bash
# deploy.sh — 安全部署脚本
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

CORE_DIRS=(
  /www/wwwroot/api.mzsh.top/agents
  /www/wwwroot/api.mzsh.top/services
  /www/wwwroot/api.mzsh.top/routers
  /www/wwwroot/api.mzsh.top/utils
  /www/wwwroot/api.mzsh.top/tools
  /www/wwwroot/api.mzsh.top/core
)

ERRORS=0; PASS=0
echo -e "${YELLOW}🔍 语法检查...${NC}"

for DIR in "${CORE_DIRS[@]}"; do
  [ ! -d "$DIR" ] && continue
  for f in "$DIR"/*.py; do
    [ -f "$f" ] || continue
    if python3 -c "import py_compile; py_compile.compile('$f', doraise=True)" 2>/dev/null; then
      PASS=$((PASS + 1))
    else
      echo -e "${RED} ❌ $f${NC}"
      python3 -c "import py_compile; py_compile.compile('$f', doraise=True)" 2>&1
      ERRORS=$((ERRORS + 1))
    fi
  done
done

echo ""
echo -e "${YELLOW}📊 $PASS 通过, $ERRORS 失败${NC}"
[ $ERRORS -gt 0 ] && { echo -e "${RED}❌ 取消部署${NC}"; exit 1; }

echo -e "${GREEN}✅ 重启 PM2...${NC}"
pm2 startOrReload /www/wwwroot/api.mzsh.top/ecosystem.config.js --update-env
sleep 3

echo -e "${YELLOW}🩺 健康检查...${NC}"
for i in 1 2 3 4 5; do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health 2>/dev/null || echo "000")
  if [ "$HTTP_CODE" = "200" ]; then
    echo -e "${GREEN}✅ 服务健康 (HTTP $HTTP_CODE)${NC}"
    exit 0
  fi
  echo "⏳ $i/5 (HTTP $HTTP_CODE)"
  sleep 2
done

echo -e "${RED}❌ 服务未就绪${NC}"
pm2 logs ai-drama-api --nostream --lines 10
exit 1
