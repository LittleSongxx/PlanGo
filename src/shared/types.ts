// 领域模型（防腐层落点）：所有外部数据（VitaBench / 高德 / Mock API）先转成这里的对象，再进 Agent/UI。
// 术语与 yoyu common/models.py 对齐，移植到 TS。
import type { SelectedPoi } from './location'

export type SourceTag = 'real' | 'browser' | 'amap' | 'user' | 'unknown' | 'dataset' | 'simulated' | 'cache' | 'fallback'

export type SceneType = 'family' | 'friends' | 'couple' | 'solo' | 'business'

// 场景需求（意图解析产物）
export interface SceneDemand {
  scene_type: SceneType
  city: string
  group_size: number
  duration_hours: number
  start_time: string // HH:MM
  budget_per_person?: number
  child_age?: number
  dietary: string[]
  interests: string[]
  special_requests: string[]
  personas: string[] // 激活的人群 Skill：减脂/带娃/经期/商务/约会/独处…
  location?: string
  raw_input: string
}

// 偏好单元（MARS preference chunk）
export interface PreferenceChunk {
  text: string
  polarity: 'positive' | 'negative'
  strength: number
  evidence_count: number
  source: 'conversation' | 'order' | 'review' | 'import'
  explicit?: boolean
  provenance?: string
}

// 周末足迹（越懂你的可视化素材：PlanGo陪你去过哪些地方）
export interface Footprint {
  date: string // 展示用日期，如 "6/21 周六"
  place: string
  scene: string // 带娃/约会/减脂/家庭聚餐…
  note?: string
}

export interface UserProfile {
  user_id: string
  summary: string
  episodes?: { id: string; text: string; scope?: string; createdAt?: string }[]
  preferences: PreferenceChunk[]
  favorite_shops: string[]
  favorite_provenance?: Record<string, { explicit: boolean; source: string }>
  avoid_shops: string[]
  home_city: string
  since?: number // 首次使用时间戳（"陪你 N 天"）
  footprints?: Footprint[] // 周末足迹时间线
}

// 供给层：统一商家对象
export interface POISummary {
  poi_id: string
  name: string
  category: string
  raw_score: number | null
  filtered_score?: number
  trust: 'high' | 'medium' | 'low' | 'unknown'
  trust_reason: string
  address: string
  city?: string
  lng?: number
  lat?: number
  price_per_person?: number
  tags: string[]
  enable_book: boolean
  enable_reservation: boolean
  business_hours: string
  products: ProductItem[]
  source: SourceTag
  image?: string
  images?: string[]
  recommended: string[]
  distance_m?: number
  rating_count?: number
  open_now?: boolean
  tel?: string
  is_distraction: boolean
  observed_at?: string
}

export interface ProductItem {
  name: string
  price: number
  tags?: string[]
  quantity?: number
  is_distraction?: boolean
}

// 规划层
export interface RouteInfo {
  mode: 'walking' | 'transit' | 'driving' | 'riding'
  distance_m: number
  duration_min: number
  taxi_cost?: number
  desc: string
}

export interface PlanNode {
  node_id: string
  time_start: string
  time_end: string
  category: 'activity' | 'dining' | 'commute' | 'rest'
  title: string
  poi?: POISummary
  reason: string
  locked: boolean
  transit_from_prev_min: number | null
  distance_km?: number | null
  distance_kind?: 'route' | 'straight_line_lower_bound'
  wait_min?: number | null
  route_from_prev?: RouteInfo
  verify_state: 'suggested' | 'verified' | 'booked'
}

export interface Plan {
  plan_id: string
  origin?: { name: string; latitude: number; longitude: number }
  travel_mode?: 'driving' | 'walking' | 'transit'
  visit_date?: string
  timezone?: string
  style: 'economic' | 'balanced' | 'premium' | 'special'
  title: string
  nodes: PlanNode[]
  total_cost: number | null
  budget_limit?: number | null // null means explicitly no spending cap, never zero.
  radar: Record<string, number> // 省钱/好玩/便捷/合适/特色，0-100（移植 weplan 确定性五维打分）
  radar_reasons?: Record<string, string> // 每一维的自然语言理由（与雷达图严格对齐）
  total_travel_min?: number // 全程通勤分钟
  total_distance_km?: number // 全程里程（km）
  share_message: string
  run_id?: string
  version?: number
  party_size?: number
  evidence?: HarnessEvidence[]
  validation_notes?: string[]
  source_mix?: Record<string, number>
}

// 工具层
export interface ToolResult<T = unknown> {
  success: boolean
  data?: T
  error?: string
  source: SourceTag
  latency_ms: number
}

// 校验层
export interface ConstraintIssue {
  code: string
  node_id?: string
  message: string
  severity: 'hard' | 'soft'
}

export interface VerifyReport {
  passed: boolean
  issues: ConstraintIssue[]
}

// —— Agent 运行过程（喂给右侧透明步骤流）——
export type StepStatus = 'running' | 'waiting' | 'idle' | 'done' | 'error'
export interface AgentStep {
  id: string
  label: string
  status: StepStatus
  detail?: string
  source?: SourceTag
}

export type ChatRole = 'user' | 'assistant' | 'system'
export interface ChatMessage {
  role: ChatRole
  content: string
}

// 成果卡片（渲染到成果区画布）
export interface DraftReviewCardDetails { runId: string; interruptId: string; planId: string; planVersion: number; unknowns: string[]; canPrepare: boolean; blockedReason?: string }

export type OutcomeCard =
  | { kind: 'draft_review'; draft: DraftReviewCardDetails }
  | { kind: 'preparation'; ready: boolean; current?: boolean; summary: string; observedAt?: string; pendingChecks?: string[]; resume?: HarnessPreparationResume & { run_id: string };
      entries: { name: string; address: string; partySize?: number; date: string; time: string; timezone: string }[];
      issues: { name: string; detail: string; mismatch: boolean }[] }
  | { kind: 'evidence'; items: HarnessEvidence[] }
  | { kind: 'browser_page'; title: string; rawTitle?: string; url: string; text: string; source?: SourceTag; observedAt?: string; scope?: 'visual_observation' | 'image_text'; limitations?: string[];
      menuCount?: number; offerCount?: number; places?: { name: string; address?: string; averagePrice?: number; priceUnit?: string; quote?: string }[] }
  | { kind: 'plan'; plan: Plan }
  | { kind: 'plans'; variants: { plan: Plan; styleLabel: string; per: number | null; overBudget?: number }[]; city: string; budget?: number }
  | { kind: 'deal'; title: string; rows: DealRow[] }
  | { kind: 'price_comparison'; title: string; source: SourceTag; data: PriceComparison }
  | { kind: 'dishes'; shopName: string; dishes: DishReco[]; mode?: 'menu' | 'recommended_dishes' | 'excerpt' | 'recommendation'; source?: SourceTag }
  | { kind: 'queue'; shopName: string; number: string; ahead: number; etaMin: number; source: SourceTag }
  | { kind: 'consensus'; planId: string; question: string; options: string[] }
  | { kind: 'receipt'; items: ReceiptItem[]; shareMessage: string }
  | { kind: 'confirm'; token: string; title: string; detail: string; danger: boolean }
  | { kind: 'takeout'; shopName: string; deliverTo: string; etaMin: number; deliveryFee: number; packFee: number; items: TakeoutItem[]; total: number; source: SourceTag }
  | { kind: 'discover'; city: string; groups: DiscoverGroup[]; source: SourceTag }
  | { kind: 'groupbuy'; shopName: string; packages: GroupBuyPackage[]; source: SourceTag }

export interface GroupBuyPackage {
  name: string
  price: number | null
  originalPrice: number | null
  includes: string[]
  fitPeople: string
  quote?: string
  sold?: string
  recommended?: boolean
}

export interface DiscoverGroup {
  label: string
  emoji: string
  items: POISummary[]
}

export interface TakeoutItem {
  name: string
  price: number
  qty: number
  reason: string
}

export interface DealRow {
  shop: string
  original: number
  final: number
  saved: number
  used: string[]
  reason: string
  credentials?: string[]
  filtered_fake?: string[]
  source: SourceTag
}

export interface DishReco {
  name: string
  price?: number
  priceUnit?: string
  reason: string
  excluded?: boolean // 避雷菜（命中人群硬约束，建议避开）
  signature?: boolean // 招牌/店内推荐
}

export interface ReceiptItem {
  run_id?: string
  action_id?: string
  resolution_required?: boolean
  business_confirmed?: boolean
  label: string
  status: 'ok' | 'fail' | 'pending'
  detail: string
  source: SourceTag
}

export interface AgentReply {
  content: string
  steps: AgentStep[]
  cards: OutcomeCard[]
  activities: string[]
}

// Persisted Planora contracts at the desktop boundary. Domain payloads are
// validated by the backend; the renderer projects only fields it recognizes.
export interface HarnessEvidence {
  evidence_id: string
  source: SourceTag
  source_ref: string
  claim: string
  observed_at?: string
  expires_at?: string
}

export interface HarnessFeedback {
  feedback_id: string
  run_id: string
  turn_id: number
  rating: 'helpful' | 'unhelpful'
  text: string
  created_at: string
}
export type HarnessFeedbackInput = Pick<HarnessFeedback, 'feedback_id' | 'turn_id' | 'rating'> & { text?: string }
export interface HarnessFeedbackReply { accepted: true; replayed: boolean; feedback: HarnessFeedback }

export interface HarnessDraftReview {
  interrupt_id: string
  plan_id: string
  plan_version: number
  can_prepare: boolean
  preparation_blockers: string[]
  unknowns: { name: string; detail?: string; passed?: boolean | null }[]
  conflicts: { name: string; detail?: string; passed?: boolean | null }[]
  scope: 'draft_ready'
}

export interface HarnessPreparationResume { plan_id: string; plan_version: number; approval_id: string; can_resume: boolean; blockers: string[] }

export interface HarnessSnapshot {
  run_id: string
  thread_id?: string
  user_id?: string
  input_text: string
  phase: string
  outcome?: string | null
  event_seq: number
  version?: number
  interrupt_id?: string | null
  command_pending?: boolean
  cancel_requested?: boolean
  preparation_resume?: HarnessPreparationResume | null
  draft_review?: HarnessDraftReview | null
  feedback?: HarnessFeedback[]
  events?: HarnessEvent[] // Main-process canonical event projection.
  state: Record<string, unknown>
}

export interface HarnessEvent {
  run_id: string
  seq: number
  event_type: string
  phase?: string
  agent_id?: string | null
  payload: Record<string, unknown>
  created_at?: string
}

export interface ModelCheck {
  status: 'not_checked' | 'passed' | 'failed' | 'not_configured'
  checked_at?: string
  category?: string
  total_tokens?: number
}

export interface ExecutionSummary {
  runtime_profile: 'desktop' | 'service'
  model: { name: string; provider_origin: string; key_configured: boolean; check: ModelCheck }
  capabilities: { amap_configured: boolean; browser_vision_enabled: boolean; browser_strategy: 'dom_first'; image_input: 'model_dependent'; transit: 'limited' }
  recent_task_model?: { run_id: string; name: string; status: string; recorded_at: string }
}

export interface HarnessStatus {
  ready: boolean
  input_delivery_version?: number
  error?: string
  service?: { origin: string; ownership: 'desktop' | 'external' }
  execution?: ExecutionSummary
}

export interface HarnessDeliveryRequest {
  requestId: string
  text: string
  image?: string
  selectedPoi?: SelectedPoi
  runId?: string
}
export interface HarnessDeliveryResult {
  requestId: string
  status: 'not_sent' | 'unconfirmed' | 'accepted' | 'delivered'
  runId?: string
  snapshot?: HarnessSnapshot
  error?: string
}

export interface HarnessApi {
  deliver: (request: HarnessDeliveryRequest) => Promise<HarnessDeliveryResult>
  checkDelivery: (requestId: string) => Promise<HarnessDeliveryResult>
  editRequirements: (runId: string, edit: RequirementEdit) => Promise<HarnessSnapshot>
  resumePreparation: (runId: string, planId: string, planVersion: number, approvalId: string) => Promise<HarnessSnapshot>
  decideDraft: (runId: string, interruptId: string, planId: string, planVersion: number, decision: 'save' | 'prepare') => Promise<HarnessSnapshot>
  feedback: (runId: string, value: HarnessFeedbackInput) => Promise<HarnessFeedbackReply>
  resolveAction: (runId: string, actionId: string, status: 'SUCCEEDED' | 'FAILED', note: string, reference?: string) => Promise<HarnessSnapshot>
  createRun: (text: string, image?: string, selectedPoi?: SelectedPoi) => Promise<HarnessSnapshot>
  getRun: (runId: string) => Promise<HarnessSnapshot>
  sendMessage: (runId: string, text: string, image?: string) => Promise<HarnessSnapshot>
  selectPlan: (runId: string, planId: string, planVersion: number) => Promise<HarnessSnapshot>
  replan: (runId: string, reason: string) => Promise<HarnessSnapshot>
  cancel: (runId: string) => Promise<HarnessSnapshot>
  resume: (runId: string, interruptId: string, decision: 'approve' | 'reject' | 'edit' | 'resume', text?: string) => Promise<HarnessSnapshot>
  listRuns: () => Promise<HarnessSnapshot[]>
  status: (checkModel?: boolean) => Promise<HarnessStatus>
}

export interface RequirementFields {
  location_name?: string
  search_location_name?: string
  max_distance_km?: number | null
  visit_date?: string | null
  time_window_start?: string | null
  party_size?: number
  budget?: number | null
  per_person_budget?: number | null
  travel_mode?: 'driving' | 'walking' | 'transit'
}

export interface RequirementEdit {
  expected_version: number
  fields?: RequirementFields
  stop_lock?: { plan_id: string; plan_version: number; place_id: string; locked: boolean }
}

export interface Reminder {
  id: string
  text: string
  at: number
  fired: boolean
}

export interface ReminderList {
  reminders: Reminder[]
  history: { id: string; text: string; ts: number; kind: 'reminder' }[]
}

export interface ReminderApi {
  list: () => Promise<ReminderList>
  create: (text: string, at: string) => Promise<ReminderList>
  remove: (id: string) => Promise<ReminderList>
}


export interface PriceComparison {
  basis: 'per_person'
  party_size: number | null
  total_budget: number | null
  entries: {
    name: string
    unit_price: number | null
    total: number | null
    within_budget: boolean | null
    source_url: string
    quote: string
    evidence_id: string
  }[]
  recommendation: string | null
  savings: number | null
  summary: string
  limitations: string[]
}
