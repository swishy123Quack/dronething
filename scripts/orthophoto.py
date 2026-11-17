from osgeo import gdal
from pathlib import Path

gdal.UseExceptions()

BASE_DIR = Path(__file__).resolve().parent

input_path = BASE_DIR.parent / "output" / "webodm" / "orthophoto.tif"
output_path = BASE_DIR.parent / "output" / "qgis" / "orthophoto.png"

print("Reading file")
# Open source GeoTIFF
src = gdal.Open(input_path)

if src is None:
    raise Exception("Could not open input TIFF!")

print("Coverting to png")

# Convert to PNG
result = gdal.Translate(
    output_path,
    src,
    format="PNG"
)

if result is None:
    raise Exception("PNG conversion failed!")

result = None
src = None

print(f"Export completed: {output_path}")
