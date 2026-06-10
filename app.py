import uuid
from pathlib import Path

import requests
from flask import Flask, flash, redirect, render_template, request, url_for
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "static" / "uploads"
RESULT_FOLDER = BASE_DIR / "static" / "results"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "bmp", "webp"}

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
RESULT_FOLDER.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = "change-this-key"

# Google reCAPTCHA v2
# Ключ сайта — вставляется в HTML-страницу.
app.config["RECAPTCHA_PUBLIC_KEY"] = "6LeakRYtAAAAAP28xdscjg8foeHRFcs6dKNBoysl"

# Секретный ключ — используется сервером для проверки ответа reCAPTCHA.
app.config["RECAPTCHA_PRIVATE_KEY"] = "6LeakRYtAAAAAL386UNE8VRR0atUTRdiXgzAvMwq"


def allowed_file(filename):
    """Проверяет, является ли файл изображением допустимого формата."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def verify_recaptcha(response_token):
    """Проверяет Google reCAPTCHA через сервер Google."""
    if not response_token:
        return False

    verify_url = "https://www.google.com/recaptcha/api/siteverify"
    payload = {
        "secret": app.config["RECAPTCHA_PRIVATE_KEY"],
        "response": response_token,
    }

    try:
        response = requests.post(verify_url, data=payload, timeout=10)
        result = response.json()
        return result.get("success", False)
    except requests.RequestException:
        return False


def get_font(size):
    """Загружает шрифт с поддержкой кириллицы."""
    font_candidates = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
        "C:/Windows/Fonts/calibri.ttf",
    ]

    for font_path in font_candidates:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size=size)

    return ImageFont.load_default()


def denoise_image(image, method, smoothing_parameter):
    """
    Выполняет устранение шума изображения.

    Пользователь задает параметр сглаживания, который влияет
    на силу фильтрации.
    """
    smoothing_parameter = max(1, min(int(smoothing_parameter), 15))

    if method == "median":
        filter_size = smoothing_parameter

        # Для медианного фильтра размер окна должен быть нечетным.
        if filter_size % 2 == 0:
            filter_size += 1

        return image.filter(ImageFilter.MedianFilter(size=filter_size))

    if method == "box":
        return image.filter(ImageFilter.BoxBlur(radius=smoothing_parameter))

    return image.filter(ImageFilter.GaussianBlur(radius=smoothing_parameter))


def get_method_name(method):
    """Возвращает русское название выбранного метода фильтрации."""
    method_names = {
        "gaussian": "Гауссов фильтр",
        "median": "Медианный фильтр",
        "box": "Усредняющий фильтр",
    }

    return method_names.get(method, "Гауссов фильтр")


def build_histogram_chart(histograms, title, output_path, channel_names):
    """Рисует график-гистограмму средствами Pillow без matplotlib и numpy."""
    width, height = 850, 520
    left, top, right, bottom = 80, 60, 40, 70
    plot_width = width - left - right
    plot_height = height - top - bottom

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    title_font = get_font(22)
    axis_font = get_font(16)
    legend_font = get_font(15)

    draw.text((left, 20), title, fill="black", font=title_font)

    draw.line(
        (left, top, left, top + plot_height),
        fill="black",
        width=2,
    )
    draw.line(
        (left, top + plot_height, left + plot_width, top + plot_height),
        fill="black",
        width=2,
    )

    draw.text(
        (left + 180, height - 35),
        "Интенсивность / величина отличия пикселя",
        fill="black",
        font=axis_font,
    )
    draw.text(
        (10, top + 180),
        "Количество",
        fill="black",
        font=axis_font,
    )

    colors = ["red", "green", "blue", "black"]
    max_value = max(max(histogram) for histogram in histograms) or 1

    for histogram_index, histogram in enumerate(histograms):
        color = colors[histogram_index % len(colors)]
        points = []

        for value, count in enumerate(histogram[:256]):
            x = left + int(value * plot_width / 255)
            y = top + plot_height - int(count * plot_height / max_value)
            points.append((x, y))

        if len(points) > 1:
            draw.line(points, fill=color, width=2)

        legend_y = top + 25 + histogram_index * 20
        draw.line(
            (left + 20, legend_y, left + 55, legend_y),
            fill=color,
            width=3,
        )
        draw.text(
            (left + 65, legend_y - 8),
            channel_names[histogram_index],
            fill="black",
            font=legend_font,
        )

    image.save(output_path)


def save_color_distribution(image, output_path):
    """Строит график распределения цветов RGB."""
    red_channel, green_channel, blue_channel = image.split()

    histograms = [
        red_channel.histogram(),
        green_channel.histogram(),
        blue_channel.histogram(),
    ]

    build_histogram_chart(
        histograms,
        "Распределение цветов изображения",
        output_path,
        ["Red", "Green", "Blue"],
    )


def save_noise_distribution(original_image, filtered_image, noise_map_path, noise_histogram_path):
    """Создает карту шума и график распределения шума."""
    noise_map = ImageChops.difference(original_image, filtered_image)
    noise_map.save(noise_map_path)

    gray_noise = noise_map.convert("L")

    build_histogram_chart(
        [gray_noise.histogram()],
        "Распределение шума",
        noise_histogram_path,
        ["Noise"],
    )


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        recaptcha_token = request.form.get("g-recaptcha-response", "")

        if not verify_recaptcha(recaptcha_token):
            flash("Проверка Google reCAPTCHA не пройдена. Подтвердите, что вы не робот.")
            return redirect(url_for("index"))

        uploaded_file = request.files.get("image")

        if uploaded_file is None or uploaded_file.filename == "":
            flash("Выберите файл изображения.")
            return redirect(url_for("index"))

        if not allowed_file(uploaded_file.filename):
            flash("Можно загружать только изображения PNG, JPG, JPEG, BMP или WEBP.")
            return redirect(url_for("index"))

        method = request.form.get("method", "gaussian")
        smoothing_parameter = request.form.get("smoothing_parameter", "3")

        try:
            smoothing_parameter = int(smoothing_parameter)
        except ValueError:
            flash("Параметр сглаживания должен быть целым числом.")
            return redirect(url_for("index"))

        run_id = uuid.uuid4().hex

        original_name = uploaded_file.filename
        extension = original_name.rsplit(".", 1)[-1].lower()

        if extension not in ALLOWED_EXTENSIONS:
            flash("Можно загружать только изображения PNG, JPG, JPEG, BMP или WEBP.")
            return redirect(url_for("index"))

        safe_name = secure_filename(original_name)

        if not safe_name or "." not in safe_name:
            safe_name = f"image.{extension}"

        original_filename = f"{run_id}_original.{extension}"
        filtered_filename = f"{run_id}_filtered.png"
        noise_map_filename = f"{run_id}_noise_map.png"
        color_hist_filename = f"{run_id}_color_hist.png"
        noise_hist_filename = f"{run_id}_noise_hist.png"

        original_path = UPLOAD_FOLDER / original_filename
        filtered_path = RESULT_FOLDER / filtered_filename
        noise_map_path = RESULT_FOLDER / noise_map_filename
        color_hist_path = RESULT_FOLDER / color_hist_filename
        noise_hist_path = RESULT_FOLDER / noise_hist_filename

        uploaded_file.save(original_path)

        original_image = Image.open(original_path).convert("RGB")
        filtered_image = denoise_image(original_image, method, smoothing_parameter)

        filtered_image.save(filtered_path)

        save_color_distribution(original_image, color_hist_path)
        save_noise_distribution(
            original_image,
            filtered_image,
            noise_map_path,
            noise_hist_path,
        )

        return render_template(
            "result.html",
            method=get_method_name(method),
            smoothing_parameter=smoothing_parameter,
            original_image=url_for("static", filename=f"uploads/{original_filename}"),
            filtered_image=url_for("static", filename=f"results/{filtered_filename}"),
            noise_map=url_for("static", filename=f"results/{noise_map_filename}"),
            color_histogram=url_for("static", filename=f"results/{color_hist_filename}"),
            noise_histogram=url_for("static", filename=f"results/{noise_hist_filename}"),
        )

    return render_template(
        "index.html",
        recaptcha_site_key=app.config["RECAPTCHA_PUBLIC_KEY"],
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)