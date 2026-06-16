import io
import os
import json
import base64
import math
import traceback
import logging
from functools import wraps
from flask import Flask, request, jsonify, send_file
from PIL import Image, ImageDraw, ImageFont
import img2pdf

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', static_url_path='')

# Настройки аутентификации
USERNAME = 'admin'
PASSWORD = 'sticker2025'

def check_auth(username, password):
    return username == USERNAME and password == PASSWORD

def authenticate():
    return jsonify({'error': 'Authentication required'}), 401, {'WWW-Authenticate': 'Basic realm="Login Required"'}

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# Папка для раскладок
LAYOUTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'layouts')
if not os.path.exists(LAYOUTS_DIR):
    os.makedirs(LAYOUTS_DIR, exist_ok=True)
    try:
        os.chmod(LAYOUTS_DIR, 0o777)
    except Exception:
        pass

def mm_to_px(mm, dpi):
    return int(round(mm * dpi / 25.4))

def resize_fit(img, size, fit, offset_x=0, offset_y=0):
    w, h = size
    if fit == 'cover':
        scale = max(w / img.width, h / img.height)
        new_w = int(img.width * scale)
        new_h = int(img.height * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - w) // 2
        top = (new_h - h) // 2
        img = img.crop((left, top, left + w, top + h))
    elif fit == 'contain':
        scale = min(w / img.width, h / img.height)
        new_w = int(img.width * scale)
        new_h = int(img.height * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        paste_x = (w - new_w) // 2
        paste_y = (h - new_h) // 2
        canvas.paste(img, (paste_x, paste_y), img)
        img = canvas
    else:
        img = img.resize((w, h), Image.LANCZOS)

    if offset_x != 0 or offset_y != 0:
        canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        canvas.paste(img, (offset_x, offset_y), img)
        img = canvas
    return img

@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/editor')
@requires_auth
def editor():
    return app.send_static_file('editor.html')

@app.route('/api/layouts', methods=['GET'])
def list_layouts():
    try:
        files = [f.replace('.json', '') for f in os.listdir(LAYOUTS_DIR) if f.endswith('.json')]
        return jsonify(files)
    except Exception as e:
        logger.error(f"List layouts error: {e}")
        return jsonify([])

@app.route('/api/layouts/<name>', methods=['GET'])
def get_layout(name):
    if '..' in name or '/' in name:
        return jsonify({'error': 'Invalid name'}), 400
    path = os.path.join(LAYOUTS_DIR, f"{name}.json")
    if not os.path.exists(path):
        return jsonify({'error': 'Layout not found'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        return jsonify(json.load(f))

@app.route('/api/layouts/<name>', methods=['POST'])
@requires_auth
def save_layout(name):
    if '..' in name or '/' in name:
        return jsonify({'error': 'Invalid name'}), 400
    data = request.get_json()
    path = os.path.join(LAYOUTS_DIR, f"{name}.json")
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        try:
            os.chmod(path, 0o666)
        except Exception:
            pass
        return jsonify({'status': 'ok'})
    except Exception as e:
        logger.error(f"Save layout error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/layouts/<name>', methods=['DELETE'])
@requires_auth
def delete_layout(name):
    if '..' in name or '/' in name:
        return jsonify({'error': 'Invalid name'}), 400
    path = os.path.join(LAYOUTS_DIR, f"{name}.json")
    if os.path.exists(path):
        os.remove(path)
        return jsonify({'status': 'deleted'})
    return jsonify({'error': 'Layout not found'}), 404

@app.route('/api/generate', methods=['POST'])
def generate():
    try:
        data = request.get_json(force=True)
        logger.info(f"Export request with {len(data.get('keys', []))} keys")

        dpi = int(data.get('dpi', 300))
        keys = data.get('keys', [])
        fmt = data.get('format', 'png').lower()
        quality = int(data.get('quality', 90))
        outer_padding_mm = float(data.get('padding', 10))

        if not keys:
            return jsonify({'error': 'No keys'}), 400

        min_x = min(k['x'] for k in keys)
        min_y = min(k['y'] for k in keys)
        max_x = max(k['x'] + k['w'] for k in keys)
        max_y = max(k['y'] + k['h'] for k in keys)
        total_w_mm = max_x - min_x
        total_h_mm = max_y - min_y

        badge_info = data.get('badge', None)
        badge_height_mm = 0.0
        badge_text = ''
        if badge_info and badge_info.get('text'):
            badge_height_mm = float(badge_info.get('height', 15))
            badge_text = badge_info.get('text', '')

        badge_area_mm = badge_height_mm + (5 if badge_height_mm > 0 else 0)

        canvas_w_mm = total_w_mm + 2 * outer_padding_mm
        canvas_h_mm = total_h_mm + 2 * outer_padding_mm + badge_area_mm

        img_w = max(1, mm_to_px(canvas_w_mm, dpi))
        img_h = max(1, mm_to_px(canvas_h_mm, dpi))
        img = Image.new('RGBA', (img_w, img_h), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)

        # 1. Фон страницы (если есть и не прозрачный)
        page_bg = data.get('pageBackground', None)
        if page_bg and page_bg.get('image') and not page_bg.get('transparent', False):
            try:
                pb_img_str = page_bg['image']
                if ',' in pb_img_str:
                    pb_img_str = pb_img_str.split(',', 1)[1]
                pb_bytes = base64.b64decode(pb_img_str)
                pb_pil = Image.open(io.BytesIO(pb_bytes)).convert('RGBA')
                pb_fit = page_bg.get('fit', 'cover')
                pb_off_x = float(page_bg.get('offsetX', 0))
                pb_off_y = float(page_bg.get('offsetY', 0))
                off_x_px = mm_to_px(pb_off_x, dpi)
                off_y_px = mm_to_px(pb_off_y, dpi)
                page_img = resize_fit(pb_pil, (img_w, img_h), pb_fit, off_x_px, off_y_px)
                img.paste(page_img, (0, 0), page_img)
            except Exception as e:
                logger.error(f"Page background error: {e}")

        # 2. Плашка
        if badge_height_mm > 0 and badge_text:
            badge_h_px = mm_to_px(badge_height_mm, dpi)
            badge_w_px = img_w
            badge_radius = max(1, mm_to_px(min(badge_height_mm * 0.2, 5), dpi))
            draw.rounded_rectangle(
                [0, 0, badge_w_px, badge_h_px],
                radius=badge_radius,
                fill='#3a3a3a'
            )
            try:
                font_size = max(1, int(badge_h_px * 0.6))
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
            except:
                font = ImageFont.load_default()
            try:
                text_bbox = draw.textbbox((0, 0), badge_text, font=font)
                text_w = text_bbox[2] - text_bbox[0]
                text_h = text_bbox[3] - text_bbox[1]
            except:
                text_w = badge_w_px // 2
                text_h = badge_h_px // 2
            text_x = (badge_w_px - text_w) // 2
            text_y = (badge_h_px - text_h) // 2 - text_bbox[1] if 'text_bbox' in locals() else 0
            draw.text((text_x, text_y), badge_text, fill='white', font=font)

        key_offset_y_mm = badge_area_mm
        key_offset_y_px = mm_to_px(key_offset_y_mm, dpi)

        # 3. Общий фон клавиш
        bg_image = None
        bg_inner_pad_mm = 0.0
        if 'background' in data and data['background'] and data['background'].get('image'):
            try:
                bg_data = data['background']
                bg_img_str = bg_data['image']
                if ',' in bg_img_str:
                    bg_img_str = bg_img_str.split(',', 1)[1]
                bg_bytes = base64.b64decode(bg_img_str)
                bg_pil = Image.open(io.BytesIO(bg_bytes)).convert('RGBA')
                bg_fit = bg_data.get('fit', 'cover')
                bg_off_x = float(bg_data.get('offsetX', 0))
                bg_off_y = float(bg_data.get('offsetY', 0))
                bg_inner_pad_mm = float(bg_data.get('innerPadding', 0.0))

                bg_w_px = max(1, mm_to_px(total_w_mm, dpi))
                bg_h_px = max(1, mm_to_px(total_h_mm, dpi))
                off_x_px = mm_to_px(bg_off_x, dpi)
                off_y_px = mm_to_px(bg_off_y, dpi)

                bg_image = resize_fit(bg_pil, (bg_w_px, bg_h_px), bg_fit, off_x_px, off_y_px)
            except Exception as e:
                logger.error(f"Background loading error: {e}")

        # 4. Клавиши
        for idx, k in enumerate(keys):
            try:
                x_px = mm_to_px(k['x'] - min_x + outer_padding_mm, dpi)
                y_px = mm_to_px(k['y'] - min_y + outer_padding_mm, dpi) + key_offset_y_px
                w_px = max(1, mm_to_px(k['w'], dpi))
                h_px = max(1, mm_to_px(k['h'], dpi))
                radius = max(1, mm_to_px(1.0, dpi))

                draw.rounded_rectangle(
                    [x_px, y_px, x_px + w_px, y_px + h_px],
                    radius=radius,
                    fill=None,
                    outline='#AAAAAA',
                    width=max(1, mm_to_px(0.3, dpi))
                )

                if bg_image:
                    bg_pad_px = mm_to_px(bg_inner_pad_mm, dpi)
                    inner_w = w_px - 2 * bg_pad_px
                    inner_h = h_px - 2 * bg_pad_px
                    if inner_w > 0 and inner_h > 0:
                        bg_x = mm_to_px(k['x'] - min_x, dpi)
                        bg_y = mm_to_px(k['y'] - min_y, dpi)
                        crop_x = bg_x + bg_pad_px
                        crop_y = bg_y + bg_pad_px
                        crop_w = w_px - 2 * bg_pad_px
                        crop_h = h_px - 2 * bg_pad_px

                        if crop_x < 0:
                            crop_w += crop_x
                            crop_x = 0
                        if crop_y < 0:
                            crop_h += crop_y
                            crop_y = 0
                        if crop_x + crop_w > bg_image.width:
                            crop_w = bg_image.width - crop_x
                        if crop_y + crop_h > bg_image.height:
                            crop_h = bg_image.height - crop_y

                        if crop_w > 0 and crop_h > 0:
                            fragment = bg_image.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))
                            if fragment.size != (inner_w, inner_h):
                                temp = Image.new('RGBA', (inner_w, inner_h), (0, 0, 0, 0))
                                paste_x = max(0, bg_pad_px - bg_x) if bg_x < bg_pad_px else 0
                                paste_y = max(0, bg_pad_px - bg_y) if bg_y < bg_pad_px else 0
                                temp.paste(fragment, (paste_x, paste_y))
                                fragment = temp

                            mask = Image.new('L', (inner_w, inner_h), 0)
                            mask_draw = ImageDraw.Draw(mask)
                            mask_draw.rounded_rectangle([0, 0, inner_w, inner_h], radius=radius, fill=255)

                            paste_x = x_px + bg_pad_px
                            paste_y = y_px + bg_pad_px
                            img.paste(fragment, (paste_x, paste_y), mask)

                if k.get('image'):
                    try:
                        inner_pad_mm = float(k.get('innerPadding', 0.3))
                        off_x_mm = float(k.get('offsetX', 0))
                        off_y_mm = float(k.get('offsetY', 0))

                        inner_pad_px = mm_to_px(inner_pad_mm, dpi)
                        inner_w = w_px - 2 * inner_pad_px
                        inner_h = h_px - 2 * inner_pad_px
                        if inner_w <= 0 or inner_h <= 0:
                            continue

                        offset_x_px = mm_to_px(off_x_mm, dpi)
                        offset_y_px = mm_to_px(off_y_mm, dpi)

                        img_str = k['image']
                        if ',' in img_str:
                            img_str = img_str.split(',', 1)[1]
                        img_bytes = base64.b64decode(img_str)
                        overlay = Image.open(io.BytesIO(img_bytes)).convert('RGBA')
                        overlay = resize_fit(overlay, (inner_w, inner_h),
                                            k.get('fit', 'cover'),
                                            offset_x_px, offset_y_px)

                        mask = Image.new('L', (inner_w, inner_h), 0)
                        mask_draw = ImageDraw.Draw(mask)
                        mask_draw.rounded_rectangle([0, 0, inner_w, inner_h], radius=radius, fill=255)

                        paste_x = x_px + inner_pad_px
                        paste_y = y_px + inner_pad_px
                        img.paste(overlay, (paste_x, paste_y), mask)
                    except Exception as e:
                        logger.error(f"Key {k.get('id', idx)} image error: {e}")
            except Exception as e:
                logger.error(f"Key {k.get('id', idx)} render error: {e}")

        # 5. Водяной знак
        watermark = data.get('watermark', None)
        if watermark and watermark.get('text'):
            try:
                wm_text = watermark['text']
                font_size = max(8, int(img_h * 0.015))
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
                except:
                    font = ImageFont.load_default()
                text_bbox = draw.textbbox((0, 0), wm_text, font=font)
                text_w = text_bbox[2] - text_bbox[0]
                text_h = text_bbox[3] - text_bbox[1]
                padding = 10
                bg_w = text_w + 2 * padding
                bg_h = text_h + 2 * padding
                x = img_w - bg_w - 5
                y = img_h - bg_h - 5

                draw.rounded_rectangle([x, y, x + bg_w, y + bg_h], radius=5, fill=(0, 0, 0, 128))
                draw.text((x + padding, y + padding), wm_text, fill='white', font=font)
            except Exception as e:
                logger.error(f"Watermark error: {e}")

        # 6. Генерация выходного файла
        output = io.BytesIO()
        if fmt == 'pdf':
            png_buffer = io.BytesIO()
            img.save(png_buffer, format='PNG')
            png_buffer.seek(0)
            page_w_pt = canvas_w_mm * 72 / 25.4
            page_h_pt = canvas_h_mm * 72 / 25.4
            layout_fun = img2pdf.get_layout_fun((page_w_pt, page_h_pt))
            pdf_bytes = img2pdf.convert(png_buffer, layout_fun=layout_fun)
            output.write(pdf_bytes)
            mimetype = 'application/pdf'
            filename = 'keyboard.pdf'
        elif fmt in ('jpg', 'jpeg'):
            rgb_img = Image.new('RGB', img.size, (255, 255, 255))
            rgb_img.paste(img, mask=img.split()[3])
            rgb_img.save(output, format='JPEG', quality=quality)
            mimetype = 'image/jpeg'
            filename = 'keyboard.jpg'
        else:
            img.save(output, format='PNG')
            mimetype = 'image/png'
            filename = 'keyboard.png'

        output.seek(0)
        return send_file(output, mimetype=mimetype, as_attachment=True, download_name=filename)

    except Exception as e:
        logger.error(f"Fatal export error: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)