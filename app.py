from flask import Flask, render_template, jsonify, request
from datetime import datetime

import sys
import json
import requests
import threading
import subprocess   

# Comment this to enable requests log
import logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = Flask(__name__)

QGIS_PYTHON_PATH = r"D:\QGis\apps\Python312\python.exe"
WEBODM_URL = "http://127.0.0.1:8000"

# Use this to test putting project files from WebODM to ./output
SKIP_ANALYSIS = False
PROJECT_ID = '70'
TASK_ID = '62d34f2d-cf3a-43e0-a887-243ae9d51b2d'

# Use this to test generating result HTML after all processing is completed
SKIP_PROCESSING = False

# Use this for low quality WEBODM result, resulting in much faster generation
LOW_QUALITY_ODM = False

project_id = 0
task_id = 0
token = 0
finished = False

logs = []
images = []

def log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    message = f"[{timestamp}] {message}"

    logs.append(message)

def convert_prog(value):
    return f'{value * 100.0}%'

def save_file(name, project_id, task_id, token):
    path = f"./output/webodm/{name}"
    res = requests.get("http://localhost:8000/api/projects/{}/tasks/{}/download/{}".format(project_id, task_id, name),
                headers={'Authorization': 'JWT {}'.format(token)},
                stream=True)
    with open(path, 'wb') as f:
        for chunk in res.iter_content(chunk_size=1024):
            if chunk:
                f.write(chunk)
    log(f"Saved {path}")

def do_analysis():
    global project_id
    global task_id
    global token

    log("Authorizing...")
    res = requests.post('http://localhost:8000/api/token-auth/',
            data={'username': 'admin',
                'password': 'admin'}).json()
    
    token = res['token']
    log(f"Authorization completed!")

    if not SKIP_ANALYSIS:
        res = requests.post('http://localhost:8000/api/projects/',
                headers={'Authorization': 'JWT {}'.format(token)},
                data={'name': f'Epic Project {datetime.now().strftime("%d/%m/%Y %H:%M:%S")}', 'public': True}).json()
        
        project_id = res['id']
        log(f"Created a new project with id = {project_id}")

        if not LOW_QUALITY_ODM:
            res = requests.post('http://localhost:8000/api/projects/{}/tasks/'.format(project_id),
                headers={'Authorization': 'JWT {}'.format(token)},
                files=images,
                data={
                    "name": f"Drone task {datetime.now():%Y-%m-%d %H:%M:%S}",
                    "options": json.dumps([
                        {"name": "orthophoto-png", "value": True},
                        {"name": "pc-classify", "value": True},
                        {"name": "dsm", "value": True},
                        {"name": "dtm", "value": True}
                    ])
                },
                timeout=3600
            ).json()
        else:
            res = requests.post('http://localhost:8000/api/projects/{}/tasks/'.format(project_id),
                headers={'Authorization': 'JWT {}'.format(token)},
                files=images,
                data={
                    "name": f"Drone task {datetime.now():%Y-%m-%d %H:%M:%S} (Low quality)",
                    "options": json.dumps([
                        {"name": "orthophoto-png", "value": True},
                        {"name": "pc-classify", "value": True},
                        {"name": "dsm", "value": True},
                        {"name": "dtm", "value": True},
                        {"name": "feature-quality", "value": "low"},
                        {"name": "pc-quality", "value": "low"},
                        {"name": "fast-orthophoto", "value": True},
                    ])
                },
                timeout=3600
            ).json()

        task_id = res['id']
        log(f"Created a new task with id = {task_id}")

        progress = [0.0, 0.0, 0.0]

        while True:
            res = requests.get('http://localhost:8000/api/projects/{}/tasks/{}/'.format(project_id, task_id),
                    headers={'Authorization': 'JWT {}'.format(token)}).json()
            
            if res['status'] == 40:
                log("Task has completed!")
                break
            elif res['status'] == 30:
                log("Task failed: {}".format(res))
                break
            else:
                crr_prog = [res['upload_progress'], res['resize_progress'], res['running_progress']]
                if crr_prog != progress:
                    log(f'Running progress: {convert_prog(res['running_progress'])} | Upload progress: {convert_prog(res['upload_progress'])} | Resize progress: {convert_prog(res['resize_progress'])}')
                progress = crr_prog
    else:
        log("Skipped analysis")
        project_id = PROJECT_ID
        task_id = TASK_ID

    if SKIP_PROCESSING:
        log("Skipped processing")
    else:
        save_file("orthophoto.tif", project_id, task_id, token)
        save_file("dsm.tif", project_id, task_id, token)
        save_file("dtm.tif", project_id, task_id, token)
        save_file("georeferenced_model.laz", project_id, task_id, token)

        for script in ["contour.py", "slope.py", "aspect.py", "hillshade.py", "orthophoto.py"]:
            for model in ["dsm", "dtm"]:
                if script == "orthophoto.py":
                    if model == "dtm":
                        break
                    log(f"Running {script}...")
                else:
                    log(f"Running {script} ({model.upper()})...")

                process = subprocess.Popen(
                    [
                        QGIS_PYTHON_PATH,
                        f"./scripts/{script}",
                        model
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )

                for line in process.stdout:
                    print(line, end="", flush=True)

                process.wait()

                if process.returncode != 0:
                    raise RuntimeError(f"{script} ({model}) failed!")

    log("Done!")

    global finished
    finished = True

from pathlib import Path
from flask import send_from_directory, url_for

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"

@app.route("/output/<path:filename>")
def output_file(filename):
    return send_from_directory(OUTPUT_DIR, filename)

@app.route("/result")
def result():
    if not finished:
        return "", 202

    return f"""
    <h3>3D Model</h3>
    <iframe scrolling="no" title="WebODM" width="61.8033%" height="360" frameBorder="0" src="http://127.0.0.1:8000/public/task/{task_id}/iframe/3d/"></iframe>

    <h3>Map</h3>
    <iframe scrolling="no" title="WebODM" width="61.8033%" height="360" frameBorder="0" src="http://127.0.0.1:8000/public/task/{task_id}/iframe/map/?t=orthophoto"></iframe>

    <h3>Topographic Map</h3>

    <h4>Digital Surface Model (DSM)</h4>
    <img src="{ url_for('output_file', filename='qgis/dsm/topographic_map.png') }" alt="Topographic Map (DSM)" style="width: 100%; max-width: 900px; height: auto;">

    <h4>Digital Terrain Model (DTM)</h4>
    <img src="{ url_for('output_file', filename='qgis/dtm/topographic_map.png') }" alt="Topographic Map (DTM)" style="width: 100%; max-width: 900px; height: auto;">

    <h3>Hillshade Map</h3>

    <h4>Digital Surface Model (DSM)</h4>
    <img src="{ url_for('output_file', filename='qgis/dsm/hillshade_map.png') }" alt="Hillshade Map (DSM)" style="width: 100%; max-width: 900px; height: auto;">

    <h4>Digital Terrain Model (DTM)</h4>
    <img src="{ url_for('output_file', filename='qgis/dtm/hillshade_map.png') }" alt="Hillshade Map (DTM)" style="width: 100%; max-width: 900px; height: auto;">

    <h3>Aspect Map</h3>

    <h4>Digital Surface Model (DSM)</h4>
    <img src="{ url_for('output_file', filename='qgis/dsm/aspect_map.png') }" alt="Aspect Map (DSM)" style="width: 100%; max-width: 900px; height: auto;">

    <h4>Digital Terrain Model (DTM)</h4>
    <img src="{ url_for('output_file', filename='qgis/dtm/aspect_map.png') }" alt="Aspect Map (DTM)" style="width: 100%; max-width: 900px; height: auto;">

    <h3>Slope Map</h3>

    <h4>Digital Surface Model (DSM)</h4>
    <img src="{ url_for('output_file', filename='qgis/dsm/slope_map.png')}" alt="Slope Map (DSM)" style="width: 100%; max-width: 900px; height: auto;">

    <h4>Digital Terrain Model (DTM)</h4>
    <img src="{ url_for('output_file', filename='qgis/dtm/slope_map.png') }" alt="Slope Map (DTM)" style="width: 100%; max-width: 900px; height: auto;">

    """, 200

@app.route("/start")
def start():
    logs.clear();
    thread = threading.Thread(target=do_analysis)
    thread.start()

    return jsonify({"message": "Analysis started"})

@app.route("/logs")
def get_logs():
    return jsonify(logs)

@app.route("/")
def home():
    global finished
    finished = False
    return render_template("index.html")

@app.route("/api/status")
def status():
    return jsonify({
        "message": "Flask backend is working!",
        "webodm": "Not connected yet",
        "qgis": "Not connected yet"
    })

@app.route("/upload", methods=["POST"])
def upload():
    global files
    files = request.files.getlist("images")
    global images
    images = [
        ("images", (file.filename, file.read(), file.mimetype))
        for file in files
    ]
    return {}

if __name__ == "__main__":
    app.run(debug=True)