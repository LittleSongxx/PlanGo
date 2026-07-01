// 内置浏览器收藏夹：本地生活常用站点的"真实入口"。
// 注意用户强调：要美团外卖/团购的实际页面，不是公司官网。
export interface Favorite {
  label: string
  url: string
  emoji: string
}

export const FAVORITES: Favorite[] = [
  { label: '美团外卖', url: 'https://waimai.meituan.com/', emoji: '🛵' },
  { label: '美团团购', url: 'https://www.meituan.com/', emoji: '🎫' },
  { label: '大众点评', url: 'https://www.dianping.com/', emoji: '⭐' },
  { label: '高德地图', url: 'https://www.amap.com/', emoji: '🧭' },
  { label: '小红书', url: 'https://www.xiaohongshu.com/explore', emoji: '📕' },
  { label: '抖音', url: 'https://www.douyin.com/', emoji: '🎬' }
]

// 默认打开页：进浏览器给一个有用的落地页（大众点评首页，便于登录读真实数据）
export const DEFAULT_HOME = 'https://www.dianping.com/'
