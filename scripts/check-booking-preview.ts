import assert from 'node:assert/strict'
import { allowedBookingPreviewRequest as allowed, isBookingPreviewUrl as preview } from '../src/shared/bookingPreview'
import { projectHarness } from '../src/renderer/src/lib/harnessProjection'
import type { HarnessSnapshot } from '../src/shared/types'

const page = 'https://www.szuo.com/en/niccolo-chongqing-tealounge/reserve/landing'
const query = '?pax=2&start_date=2026-09-11&start_time=15%3A00'
for (const suffix of ['', '/message', '/landing']) {
  for (const prefix of ['', 'shops/']) assert(preview(`https://www.szuo.com/en/${prefix}niccolo-chongqing-tealounge/reserve${suffix}`))
}
assert(preview(page + query))
assert(preview(page + '?start_time=15:00&start_date=2024-02-29&pax=12'))
assert(allowed(page + query, 'GET', 'mainFrame'))
assert(allowed(page, 'HEAD', 'mainFrame'))
for (const url of [
  page.replace('https:', 'http:'), page.replace('www.', ''), page.replace('www.', 'evil.'),
  page.replace('www.szuo.com', 'www.szuo.com.evil.test'), page.replace('www.szuo.com', 'www.szuo.com:8443'),
  page.replace('www.szuo.com', 'user:password@www.szuo.com'), page.replace('tealounge', 'other-shop'),
  page.replace('/landing', '/review'), page + '#payment', page + '?pax=2', page + query + '&pax=2',
  page + query + '&redirect=https://evil.test', page + query.replace('pax=2', 'pax=0'),
  page + query.replace('pax=2', 'pax=13'), page + query.replace('pax=2', 'pax=2.0'),
  ...['2026-02-29', '2026-13-11', '2026-00-11', '2026-09-00', '2026-04-31', '26-09-11'].map(date => page + query.replace('2026-09-11', date)),
  ...['24%3A00', '15%3A60', '3%3A00', '15%3A00%3A00'].map(time => page + query.replace('15%3A00', time)),
  'javascript:alert(1)', 'not a URL'
]) assert(!preview(url), url)

const scripts = 'https://booking-cdn.szuo.com/china/static/booking/js/client.ad6e8f36.js'
assert(allowed(scripts, 'GET', 'script'))
assert(allowed('https://booking-cdn.szuo.com/china/static/booking/css/LoadablePhoneInput.744ac43d.chunk.css', 'GET', 'stylesheet'))
assert(allowed('https://cdn0.szuo.com/common/css/tablekit-font-faces.v1.min.css', 'GET', 'stylesheet'))
assert(allowed('https://cdn3.szuo.com/common/fonts/ibmplex/v5.1.3/IBMPlex-Sans/IBMPlexSans-Regular.woff2', 'GET', 'font'))
assert(allowed('https://cdn0.tablecheck.com/common/fonts/tablekit/tablekit.woff', 'HEAD', 'font'))
const picture = 'https://2.image.cdn.szuo.com/unsafe/fit-in/1920x1080/filters:format(webp)/https://cdn2.szuo.com/booking_themes/65768d84970ba900019fa28b/canvas_images/xl/a552f01f.jpg?1702268294'
assert(allowed(picture, 'GET', 'image'))
for (const type of ['xhr', 'fetch', 'webSocket', 'ping', 'subFrame', 'other']) assert(!allowed(scripts, 'GET', type), type)
for (const method of ['POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS']) assert(!allowed(page, method, 'mainFrame') && !allowed(scripts, method, 'script'), method)
for (const [url, type] of [
  ['https://production-booking.szuo.com/v2/booking/cart/init', 'script'],
  ['https://www.szuo.com/api/cart.png', 'image'], ['https://cdn0.szuo.com/api/cart.png', 'image'],
  ['https://cdn0.szuo.com/common/images/api/payment.png', 'image'],
  ['https://booking-cdn.szuo.com/china/static/booking/api/cart.js', 'script'],
  [scripts.replace('booking-cdn.szuo.com', 'booking-cdn.szuo.com.evil.test'), 'script'],
  [scripts.replace('/js/client.', '/js/%2fapi%2fcart.'), 'script'],
  [scripts + '?action=cart', 'script'], [scripts, 'image'], [picture.replace('https://cdn2.szuo.com/', 'https://evil.test/'), 'image'],
  ['https://umami-next.post.tablecheck.com/script.js', 'script'], ['https://browser.sentry-cdn.com/6.19.6/bundle.tracing.min.js', 'script'],
  ['data:text/javascript,alert(1)', 'script']
]) assert(!allowed(url, 'GET', type), url)
console.log('Booking preview policy: exact page/parameters, static resources and blocked business requests passed')
const run: HarnessSnapshot = { run_id: 'controlled-preview', input_text: '受控参数预览', phase: 'SUCCEEDED', event_seq: 1, state: {
  execution_outcome: { kind: 'page_read', status: 'satisfied', data: { scope: 'booking_parameters', business_completed: false, availability_checked: false,
    requested: { party_size: 2, date: '2026-09-11', time: '15:00' }, visible_labels: { party_label: '2 Guests', date_label: 'Fri Sep 11', time_label: '3:00 pm' }, source_url: page + query } }
} }
const card = projectHarness(run).cards.find(card => card.kind === 'booking_preview')
assert(card?.kind === 'booking_preview' && card.complete && card.partySize === 2 && card.time === '15:00')
const pending = projectHarness({ ...run, command_pending: true }).cards.find(card => card.kind === 'booking_preview')
assert(pending?.kind === 'booking_preview' && !pending.complete, 'New pending input cannot promote the old preview')
