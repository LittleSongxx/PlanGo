/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/renderer/index.html', './src/renderer/src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: '#bce8d4',
          ink: '#183e32',
          soft: '#edf7f1',
          strong: '#296c53'
        },
        mt: { yellow: '#ffd100', dark: '#232323' }
      },
      boxShadow: {
        panel: '0 4px 24px -12px rgba(24, 62, 50, 0.18)',
        card: '0 2px 12px -6px rgba(24, 62, 50, 0.12)'
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'PingFang SC', 'Microsoft YaHei', 'system-ui', 'sans-serif']
      }
    }
  },
  plugins: []
}
