// 人群/场景 Persona（结构化"真理源"）。硬约束 by construction 直接进 Verifier；软偏好/查询词进检索。
// 移植自 yoyu/infrastructure/skills/persona.py，并做成可插拔（skills/ 目录可覆盖/扩展）。

export interface PersonaSkill {
  name: string
  advert: string
  query_keywords: string[]
  hard_exclude_tags: string[]
  soft_prefer_tags: string[]
  safety_note: string
  rules_text: string
}

export const PERSONA_SKILLS: Record<string, PersonaSkill> = {
  减脂: {
    name: '减脂',
    advert: '减脂饮食：低卡轻食优先，排除火锅烧烤甜品自助',
    query_keywords: ['轻食', '沙拉', '健康餐', '低卡', '蒸', '粗粮'],
    hard_exclude_tags: ['火锅', '烧烤', '自助', '炸', '甜品', '蛋糕店', '奶茶'],
    soft_prefer_tags: ['轻食', '沙拉', '低卡', '健康', '蒸', '高蛋白'],
    safety_note: '减脂建议为一般饮食参考，非医疗/营养处方。',
    rules_text: '低GI、高蛋白、控人均热量；优先蒸煮、轻食沙拉；避免油炸、高糖、自助暴食。'
  },
  带娃: {
    name: '带娃',
    advert: '带娃(适龄)：亲子友好、安全、不太晚，排除酒吧密室等危险场所',
    query_keywords: ['亲子', '儿童', '乐园', '亲子餐厅', '科技馆', '动物园'],
    hard_exclude_tags: ['酒吧', '密室', 'livehouse', '夜店', '清吧'],
    soft_prefer_tags: ['亲子友好', '儿童', '母婴室', '适合儿童', '宽敞'],
    safety_note: '带娃需关注适龄与安全，避免危险/嘈杂/过晚活动。',
    rules_text: '适龄优先；需亲子友好/有母婴室；活动不宜过晚(建议20:00前结束)；排除危险/成人向场所。'
  },
  经期关怀: {
    name: '经期关怀',
    advert: '经期关怀：温热饮食、补铁补血，忌生冷辛辣冰饮',
    query_keywords: ['热汤', '温补', '粥', '红糖', '姜茶', '暖'],
    hard_exclude_tags: ['冰', '冷饮', '生冷', '麻辣', '冰淇淋'],
    soft_prefer_tags: ['温热', '汤', '补铁', '暖胃', '红糖', '姜'],
    safety_note: '经期关怀建议为生活关怀参考，非医疗诊断；不适请就医。',
    rules_text: '温热为主、补铁补血(红枣/红糖/瘦肉/菠菜)；忌生冷、冰饮、过度辛辣；节奏舒缓不剧烈。'
  },
  商务宴请: {
    name: '商务宴请',
    advert: '商务宴请：有包间、安静有档次、可停车开票',
    query_keywords: ['包间', '商务宴请', '正餐', '粤菜', '本帮', '私房菜'],
    hard_exclude_tags: ['路边摊', '快餐', '嘈杂'],
    soft_prefer_tags: ['包间', '安静', '有档次', '停车方便', '可开发票', '环境好'],
    safety_note: '',
    rules_text: '需包间/安静/有档次；人均符合商务规格；停车方便、可开发票优先。'
  },
  约会: {
    name: '约会',
    advert: '约会：氛围浪漫、评分高、可加惊喜(鲜花蛋糕)',
    query_keywords: ['浪漫', '氛围', '西餐', '情调', '夜景', '咖啡'],
    hard_exclude_tags: ['嘈杂', '快餐', '路边摊'],
    soft_prefer_tags: ['浪漫', '氛围好', '环境优', '评分高', '适合情侣', '夜景'],
    safety_note: '',
    rules_text: '氛围浪漫/安静/评分高；可在用餐节点加惊喜(送花/蛋糕)；节奏从容。'
  },
  独处: {
    name: '独处',
    advert: '独处一人食：一人友好、吧台、快、小份、性价比',
    query_keywords: ['一人食', '吧台', '小份', '单人', '性价比'],
    hard_exclude_tags: [],
    soft_prefer_tags: ['一人友好', '吧台', '小份', '快', '性价比', '安静'],
    safety_note: '',
    rules_text: '一人友好(吧台/小份/单人套餐)；高效不拖沓；性价比优先；可安排独处友好的展览/书店/citywalk。'
  },
  陪长辈: {
    name: '陪长辈',
    advert: '陪长辈：清淡好嚼、安静好停车、少走路、电梯/无障碍',
    query_keywords: ['清淡', '粤菜', '本帮菜', '茶楼', '公园', '园林', '养生'],
    hard_exclude_tags: ['蹦迪', '酒吧', '密室', '网红辣', '夜店'],
    soft_prefer_tags: ['清淡', '安静', '好停车', '无障碍', '电梯', '座位舒适', '好嚼'],
    safety_note: '陪长辈以舒适安全为先，避免久走、爬坡与重辛辣。',
    rules_text: '口味清淡好咀嚼；环境安静、座位舒适、有电梯/无障碍；步行少、节奏慢、不宜过晚。'
  },
  解压: {
    name: '解压',
    advert: '解压放松：疗愈安静、自然/SPA/咖啡书店，避开嘈杂赶场',
    query_keywords: ['按摩', 'SPA', '温泉', '公园', '湖', '咖啡', '书店', '茶', '瑜伽'],
    hard_exclude_tags: ['嘈杂', '排队久'],
    soft_prefer_tags: ['安静', '疗愈', '自然', '舒缓', '氛围好', '采光好'],
    safety_note: '',
    rules_text: '以舒缓疗愈为主(自然/SPA/咖啡书店/温泉)；节奏慢、不赶场、不排长队；环境安静采光好。'
  },
  待客: {
    name: '待客',
    advert: '待客(外地朋友)：本地特色+地标打卡，体验感强、好出片',
    query_keywords: ['本地特色', '地标', '网红', '老字号', '特色小吃', '打卡', '夜景'],
    hard_exclude_tags: ['连锁快餐'],
    soft_prefer_tags: ['本地特色', '地标', '好出片', '体验感', '老字号', '夜景'],
    safety_note: '',
    rules_text: '突出本地特色与地标(老字号+网红打卡)；体验感强、好出片；动线顺、含一处城市名片。'
  }
}

export interface MergedConstraints {
  hard_exclude: string[]
  soft_prefer: string[]
  query_keywords: string[]
  safety_notes: string[]
}

export function loadPersonas(names: string[]): PersonaSkill[] {
  return names.map((n) => PERSONA_SKILLS[n]).filter(Boolean)
}

export function personasPrompt(names: string[]): string {
  const skills = loadPersonas(names)
  if (!skills.length) return ''
  return skills.map((s) => `- [${s.name}] ${s.rules_text}${s.safety_note ? `（${s.safety_note}）` : ''}`).join('\n')
}

export function mergedConstraints(names: string[]): MergedConstraints {
  const skills = loadPersonas(names)
  const hard = new Set<string>()
  const soft = new Set<string>()
  const kws = new Set<string>()
  const safety: string[] = []
  for (const s of skills) {
    s.hard_exclude_tags.forEach((t) => hard.add(t))
    s.soft_prefer_tags.forEach((t) => soft.add(t))
    s.query_keywords.forEach((t) => kws.add(t))
    if (s.safety_note) safety.push(s.safety_note)
  }
  return {
    hard_exclude: [...hard].sort(),
    soft_prefer: [...soft].sort(),
    // 关键词保留人群插入顺序（活动/饮食路由靠顺序，别排序打乱）
    query_keywords: [...kws],
    safety_notes: safety
  }
}

// 从自由文本粗解析人群（意图兜底，LLM 主解析失败时用）
export function inferPersonas(text: string): string[] {
  const t = text || ''
  const out: string[] = []
  if (/减脂|减肥|轻食|低卡|健身餐/.test(t)) out.push('减脂')
  if (/带娃|孩子|小孩|儿童|亲子|宝宝|岁/.test(t)) out.push('带娃')
  if (/经期|大姨妈|生理期/.test(t)) out.push('经期关怀')
  if (/商务|宴请|客户|请客/.test(t)) out.push('商务宴请')
  if (/约会|女朋友|男朋友|情侣|对象|纪念日/.test(t)) out.push('约会')
  if (/一个人|独自|自己/.test(t)) out.push('独处')
  if (/长辈|父母|爸妈|老人|爷爷|奶奶/.test(t)) out.push('陪长辈')
  if (/解压|放松|疗愈|累了/.test(t)) out.push('解压')
  if (/外地朋友|招待|来玩|待客/.test(t)) out.push('待客')
  return out
}
