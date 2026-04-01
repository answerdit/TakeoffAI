FROM nginx:alpine

# Bake static files and nginx config directly into the image
COPY frontend/dist/ /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/conf.d/default.conf
