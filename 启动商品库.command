#!/bin/zsh
cd "${0:A:h}" || exit 1
if [[ -x .venv/bin/python ]]; then
  catalog_python="$PWD/.venv/bin/python"
elif [[ -x /opt/anaconda3/bin/python3 ]]; then
  catalog_python="/opt/anaconda3/bin/python3"
else
  catalog_python="python3"
fi
if ! "$catalog_python" -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
  print '请先按照 README.md 安装依赖。'
  read '?按回车关闭…'
  exit 1
fi
print '商品库地址：http://127.0.0.1:8765'
if curl -fsS 'http://127.0.0.1:8765/api/health' >/dev/null 2>&1; then
  open 'http://127.0.0.1:8765'
  print '商品库服务已在运行，已打开页面。'
  exit 0
fi
if [[ ! -f web/dist/index.html ]]; then
  print '前端尚未构建。请按 README.md 在 frontend 目录运行 npm ci 和 npm run build。'
  read '?按回车关闭…'
  exit 1
fi
print '服务运行期间请保留本窗口，按 Ctrl+C 停止。'
(
  for attempt in {1..40}; do
    if curl -fsS 'http://127.0.0.1:8765/api/health' >/dev/null 2>&1; then
      open 'http://127.0.0.1:8765'
      exit 0
    fi
    sleep 0.5
  done
) &
"$catalog_python" -m catalog serve
read '?服务已停止，按回车关闭…'
