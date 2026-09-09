/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/renderer/index.html', './src/renderer/src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: '#ffd100',
          ink: '#2c2924',
          soft: '#fff8db',
          strong: '#8a5a00',
          hover: '#f3c300'
        },
        mt: { yellow: '#ffd100', dark: '#232323' }
      },
      boxShadow: {
        panel: '0 4px 24px -12px rgba(48, 41, 21, 0.16)',
        card: '0 2px 12px -6px rgba(48, 41, 21, 0.12)'
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'PingFang SC', 'Microsoft YaHei', 'system-ui', 'sans-serif']
      }
    }
  },
  plugins: []
}
