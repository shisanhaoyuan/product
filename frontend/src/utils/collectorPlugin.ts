import manifest from '../../../browser-helper/manifest.json';

// The downloaded extension and every frontend entry share the manifest version.
export const collectorPluginVersion = manifest.version;
export const collectorPluginDownload = '/api/products/images/helper.zip';
export const collectorPluginGuide = '/assets/capture-helper.html';
export const collectorPluginUpgrade = `请下载集物采集插件 ${collectorPluginVersion}，替换原扩展文件，在扩展管理中重新加载，再刷新商品库及已打开的 Amazon 页面。`;

export function compatibleCollectorPlugin(
  data: {
    version?: unknown;
    modes?: unknown;
    backgroundSource?: unknown;
    currentProductImages?: unknown;
  },
  mode: 'images' | 'source' | 'import',
) {
  if (typeof data.version !== 'string' || !/^\d+(\.\d+){2,3}$/.test(data.version)) return false;
  const actual = data.version.split('.').map(Number);
  const required = collectorPluginVersion.split('.').map(Number);
  let current = true;
  for (let index = 0; index < Math.max(actual.length, required.length); index++) {
    const difference = (actual[index] || 0) - (required[index] || 0);
    if (difference) {
      current = difference > 0;
      break;
    }
  }
  return (
    current &&
    Array.isArray(data.modes) &&
    data.modes.includes(mode) &&
    data.currentProductImages === true &&
    (mode === 'images' || data.backgroundSource === true)
  );
}
