/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/renderer/index.html', './src/renderer/src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: '#ffd100',
          ink: '#1a1a1a',
          soft: '#fff7d6'
        },
        mt: { yellow: '#ffd100', dark: '#232323' }
      },
      fontFamily: {
        sans: ['-apple-system', 'PingFang SC', 'Microsoft YaHei', 'system-ui', 'sans-serif']
      }
    }
  },
  plugins: []
}
