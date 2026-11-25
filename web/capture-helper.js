fetch('/api/products/images/helper-info', { cache: 'no-store' })
  .then(response => {
    if (!response.ok) throw new Error('unavailable');
    return response.json();
  })
  .then(info => {
    document.getElementById('plugin-version').textContent = `当前下载版本：${info.version} · ${info.name}`;
  })
  .catch(() => {
    document.getElementById('plugin-version').textContent = '暂时无法读取版本，请登录商品库后刷新，或在下载包 manifest.json 中查看版本。';
  });
