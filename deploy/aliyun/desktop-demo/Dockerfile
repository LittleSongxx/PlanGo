# PlanGo 桌面演示容器：Electron 桌面跑在 Xvfb 虚拟显示里，
# x11vnc + websockify 把画面以 noVNC 暴露，浏览器打开即可遥控完整桌面应用。
# 只为求职作品集演示，不是生产部署形态；密钥一律经环境变量注入，不写入镜像。
FROM node:22-bookworm

# 国内 ECS 构建加速（本机构建同样可用）
ENV ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb x11vnc novnc websockify \
    fonts-noto-cjk fonts-noto-color-emoji \
    libgtk-3-0 libnotify4 libnss3 libxss1 libxtst6 xdg-utils \
    libatspi2.0-0 libdrm2 libgbm1 libasound2 libx11-xcb1 \
    xauth procps \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --registry=https://registry.npmmirror.com \
  && npm exec -- install-electron --no
COPY . .
RUN npm run build

ENV DISPLAY=:99
EXPOSE 6080
ENTRYPOINT ["./deploy/aliyun/desktop-demo/entrypoint.sh"]
