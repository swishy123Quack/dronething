import os, sys, math
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

from qgis.core import (
    QgsApplication,
    QgsProject,
    QgsRasterLayer,
    QgsPrintLayout,
    QgsLayoutItemMap,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLayoutExporter,
    QgsSingleBandPseudoColorRenderer,
    QgsColorRampShader,
    QgsRasterShader,
    QgsCoordinateReferenceSystem,
    QgsLayoutItemMapGrid,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsLayoutItemLabel,
    QgsLayoutItemShape,
    QgsLayoutItemScaleBar,
    QgsUnitTypes,
    QgsFillSymbol,
    QgsLayoutItemPicture,
    QgsLineSymbol,
    QgsTextFormat
)

from qgis.PyQt.QtGui import QColor, QFont


# ============================================================
# CONFIGURATION
# ============================================================

import argparse
parser = argparse.ArgumentParser()

parser.add_argument(
    "model",
    choices=["dsm", "dtm"],
    help="Elevation model to process"
)
args = parser.parse_args()

MODEL = args.model
MODEL_NAME = "Digital Surface Model" if MODEL == "dsm" else "Digital Terrain Model"
BASE_DIR = Path(__file__).resolve().parent

DSM_PATH = BASE_DIR.parent / "output" / "webodm" / f"{MODEL}.tif"
ASPECT_PATH = BASE_DIR.parent / "output" / "qgis" / "temp" / "aspect.tif"
OUTPUT_PATH = BASE_DIR.parent / "output" / "qgis" / f"{MODEL}" / "aspect_map.png"

NORTH_ARROW_PATH = BASE_DIR / "north_arrow.jpg"

QGIS_PREFIX = r"D:\QGis\apps\qgis"
PLUGIN_PATH = r"D:\QGis\apps\qgis\python\plugins"

PAGE_WIDTH = 297
PAGE_HEIGHT = 210

MAP_X = 10
MAP_Y = 10
MAP_WIDTH = 180
MAP_HEIGHT = 190

EXPORT_DPI = 300


# ============================================================
# INITIALIZE QGIS
# ============================================================

print("Initializing QGIS...")

QgsApplication.setPrefixPath(QGIS_PREFIX, True)

qgs = QgsApplication([], False)
qgs.initQgis()

print("QGIS initialized")


# ============================================================
# GENERATE ASPECT RASTER
# ============================================================

if not DSM_PATH.exists():
    raise FileNotFoundError(f"DSM not found: {DSM_PATH}")

if not NORTH_ARROW_PATH.exists():
    raise FileNotFoundError(
        f"North arrow image not found: {NORTH_ARROW_PATH}"
    )

print("\nGenerating aspect raster...")

if ASPECT_PATH.exists():
    ASPECT_PATH.unlink()

# Aspect is measured clockwise from north.
# Flat areas are assigned NoData.
aspect_options = gdal.DEMProcessingOptions(
    format="GTiff",
    computeEdges=True,
    alg="Horn",
    zeroForFlat=False,
    creationOptions=[
        "COMPRESS=LZW",
        "TILED=YES"
    ]
)

result = gdal.DEMProcessing(
    str(ASPECT_PATH),
    str(DSM_PATH),
    "aspect",
    options=aspect_options
)

if result is None:
    raise RuntimeError("Failed to generate aspect raster")

result = None

print("Aspect raster generated:", ASPECT_PATH)


# ============================================================
# LOAD RASTERS
# ============================================================

project = QgsProject.instance()
project.clear()

aspect_layer = QgsRasterLayer(
    str(ASPECT_PATH),
    "Aspect"
)

if not aspect_layer.isValid():
    raise RuntimeError("Failed to load aspect raster")

project.addMapLayer(aspect_layer)
project.setCrs(aspect_layer.crs())

print("Aspect layer loaded")


# ============================================================
# INSPECT DSM AND ASPECT VALUES
# ============================================================

print("\n--- ASPECT DEBUG ---")

dsm_src = gdal.Open(str(DSM_PATH), gdal.GA_ReadOnly)
aspect_src = gdal.Open(str(ASPECT_PATH), gdal.GA_ReadOnly)

if dsm_src is None or aspect_src is None:
    raise RuntimeError("Could not open raster datasets")

dsm_band = dsm_src.GetRasterBand(1)
aspect_band = aspect_src.GetRasterBand(1)

dsm_arr = dsm_band.ReadAsArray()
aspect_arr = aspect_band.ReadAsArray()

dsm_nodata = dsm_band.GetNoDataValue()
aspect_nodata = aspect_band.GetNoDataValue()

dsm_mask = np.isfinite(dsm_arr)

if dsm_nodata is not None:
    dsm_mask &= (dsm_arr != dsm_nodata)

aspect_mask = np.isfinite(aspect_arr)

if aspect_nodata is not None:
    aspect_mask &= (aspect_arr != aspect_nodata)

# Valid aspect values should be between 0 and 360 degrees.
aspect_mask &= (aspect_arr >= 0)
aspect_mask &= (aspect_arr <= 360)

valid_aspect = aspect_arr[aspect_mask]

if valid_aspect.size == 0:
    raise RuntimeError("Aspect raster contains no valid pixels")

minimum = float(valid_aspect.min())
maximum = float(valid_aspect.max())

print("Dimensions:", aspect_src.RasterXSize, "x", aspect_src.RasterYSize)
print("CRS:", aspect_layer.crs().authid())
print("Aspect NoData:", aspect_nodata)
print("Valid aspect pixels:", valid_aspect.size)
print("Minimum aspect:", minimum)
print("Maximum aspect:", maximum)

if not aspect_layer.crs().isValid():
    raise RuntimeError("Aspect raster has no valid CRS")

if aspect_layer.crs().mapUnits() == QgsUnitTypes.DistanceDegrees:
    raise RuntimeError(
        "A projected CRS is required for accurate map measurements."
    )

# ============================================================
# CALCULATE RASTER STATISTICS
# ============================================================

mean_dsm_elevation = float(np.mean(dsm_arr[dsm_mask]))

geotransform = dsm_src.GetGeoTransform()

pixel_width = math.hypot(
    geotransform[1],
    geotransform[4]
)

pixel_height = math.hypot(
    geotransform[2],
    geotransform[5]
)

srs = osr.SpatialReference()
srs.ImportFromWkt(dsm_src.GetProjection())

unit_to_meters = srs.GetLinearUnits()

resolution_x = pixel_width * unit_to_meters
resolution_y = pixel_height * unit_to_meters

pixel_area = abs(
    geotransform[1] * geotransform[5]
    - geotransform[2] * geotransform[4]
) * unit_to_meters ** 2

survey_area_m2 = int(np.count_nonzero(dsm_mask)) * pixel_area
survey_area_ha = survey_area_m2 / 10000

aspect_area_m2 = int(np.count_nonzero(aspect_mask)) * pixel_area

aspect_coverage = (
    np.count_nonzero(aspect_mask)
    / max(np.count_nonzero(dsm_mask), 1)
) * 100

print("\n--- ASPECT STATISTICS ---")
print(f"Mean DSM elevation: {mean_dsm_elevation:.2f} m")
print(f"Raster resolution: {resolution_x:.3f} x {resolution_y:.3f} m")
print(f"Survey area: {survey_area_m2:.2f} m²")
print(f"Survey area: {survey_area_ha:.4f} ha")
print(f"Aspect coverage: {aspect_coverage:.2f}%")


# ============================================================
# STYLE ASPECT RASTER
# ============================================================

print("\nStyling aspect raster...")

# Circular color palette:
# The color at 360 degrees is identical to 0 degrees.
# This ensures a smooth transition across north.

aspect_colors = [
    (0,   "#2C7BB6", "North"),
    (45,  "#00A6CA", "Northeast"),
    (90,  "#00CC96", "East"),
    (135, "#A8D95B", "Southeast"),
    (180, "#F9D057", "South"),
    (225, "#F29E4C", "Southwest"),
    (270, "#E65C5C", "West"),
    (315, "#A66CC5", "Northwest"),
    (360, "#2C7BB6", "North")
]

shader_function = QgsColorRampShader()

shader_function.setColorRampType(
    QgsColorRampShader.Interpolated
)

ramp_items = []

for value, color, label in aspect_colors:

    ramp_items.append(
        QgsColorRampShader.ColorRampItem(
            value,
            QColor(color),
            label
        )
    )

shader_function.setColorRampItemList(ramp_items)

raster_shader = QgsRasterShader()
raster_shader.setRasterShaderFunction(shader_function)

renderer = QgsSingleBandPseudoColorRenderer(
    aspect_layer.dataProvider(),
    1,
    raster_shader
)

renderer.setClassificationMin(0)
renderer.setClassificationMax(360)

aspect_layer.setRenderer(renderer)
aspect_layer.triggerRepaint()

print("Aspect renderer configured: Interpolated")
print("Aspect range: 0 - 360 degrees")


# ============================================================
# CREATE PRINT LAYOUT
# ============================================================

print("\nCreating layout...")

layout = QgsPrintLayout(project)
layout.initializeDefaults()

layout.setName("Aspect Map")

page = layout.pageCollection().pages()[0]

page.setPageSize(
    QgsLayoutSize(PAGE_WIDTH, PAGE_HEIGHT)
)

map_item = QgsLayoutItemMap(layout)

layout.addLayoutItem(map_item)

map_item.setLayers([aspect_layer])
map_item.setKeepLayerSet(True)

map_item.attemptMove(
    QgsLayoutPoint(MAP_X, MAP_Y)
)

map_item.attemptResize(
    QgsLayoutSize(MAP_WIDTH, MAP_HEIGHT)
)

extent = aspect_layer.extent()
extent.scale(1.15)

map_item.zoomToExtent(extent)
map_item.setFrameEnabled(True)

project.layoutManager().addLayout(layout)


# ============================================================
# GEOGRAPHIC COORDINATE GRID
# ============================================================

def calculate_grid_interval(map_item, target_x=8, target_y=5):

    geographic_crs = QgsCoordinateReferenceSystem("EPSG:4326")

    transform = QgsCoordinateTransform(
        map_item.crs(),
        geographic_crs,
        QgsProject.instance()
    )

    extent = map_item.extent()

    corners = [
        QgsPointXY(extent.xMinimum(), extent.yMinimum()),
        QgsPointXY(extent.xMinimum(), extent.yMaximum()),
        QgsPointXY(extent.xMaximum(), extent.yMinimum()),
        QgsPointXY(extent.xMaximum(), extent.yMaximum())
    ]

    transformed = [transform.transform(p) for p in corners]

    lon_range = (
        max(p.x() for p in transformed)
        - min(p.x() for p in transformed)
    )

    lat_range = (
        max(p.y() for p in transformed)
        - min(p.y() for p in transformed)
    )

    def nice_interval(value):

        exponent = math.floor(math.log10(value))
        magnitude = 10 ** exponent

        candidates = [
            magnitude,
            2 * magnitude,
            5 * magnitude,
            10 * magnitude
        ]

        return min(candidates, key=lambda x: abs(x - value))

    lon_interval = nice_interval(lon_range / target_x)
    lat_interval = nice_interval(lat_range / target_y)

    print(f"Longitude interval: {lon_interval}")
    print(f"Latitude interval: {lat_interval}")

    return lon_interval, lat_interval


grid = QgsLayoutItemMapGrid("Geographic Coordinates", map_item)

map_item.grids().addGrid(grid)

grid.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))

lon_interval, lat_interval = calculate_grid_interval(
    map_item,
    target_x=4,
    target_y=6
)

grid.setIntervalX(lon_interval)
grid.setIntervalY(lat_interval)

grid.setStyle(QgsLayoutItemMapGrid.FrameAnnotationsOnly)

grid.setFrameStyle(QgsLayoutItemMapGrid.ExteriorTicks)
grid.setFrameWidth(1.5)
grid.setFramePenSize(0.2)
grid.setFramePenColor(QColor("#000000"))

grid.setAnnotationEnabled(True)

grid.setAnnotationFormat(
    QgsLayoutItemMapGrid.DecimalWithSuffix
)

grid.setAnnotationPrecision(4)

for side in [
    QgsLayoutItemMapGrid.Left,
    QgsLayoutItemMapGrid.Right,
    QgsLayoutItemMapGrid.Top,
    QgsLayoutItemMapGrid.Bottom
]:
    grid.setAnnotationPosition(
        QgsLayoutItemMapGrid.OutsideMapFrame,
        side
    )

grid.setAnnotationDisplay(
    QgsLayoutItemMapGrid.LongitudeOnly,
    QgsLayoutItemMapGrid.Top
)

grid.setAnnotationDisplay(
    QgsLayoutItemMapGrid.LongitudeOnly,
    QgsLayoutItemMapGrid.Bottom
)

grid.setAnnotationDisplay(
    QgsLayoutItemMapGrid.LatitudeOnly,
    QgsLayoutItemMapGrid.Left
)

grid.setAnnotationDisplay(
    QgsLayoutItemMapGrid.LatitudeOnly,
    QgsLayoutItemMapGrid.Right
)

grid.setAnnotationDirection(
    QgsLayoutItemMapGrid.Horizontal,
    QgsLayoutItemMapGrid.Top
)

grid.setAnnotationDirection(
    QgsLayoutItemMapGrid.Horizontal,
    QgsLayoutItemMapGrid.Bottom
)

grid.setAnnotationDirection(
    QgsLayoutItemMapGrid.Vertical,
    QgsLayoutItemMapGrid.Left
)

grid.setAnnotationDirection(
    QgsLayoutItemMapGrid.Vertical,
    QgsLayoutItemMapGrid.Right
)

coordinate_format = QgsTextFormat()
coordinate_format.setFont(QFont("Arial"))
coordinate_format.setSize(7)
coordinate_format.setColor(QColor("#000000"))

grid.setAnnotationTextFormat(coordinate_format)
grid.setAnnotationFrameDistance(1.5)
grid.setEnabled(True)

print("Geographic coordinate grid added")


# ============================================================
# TITLE, LEGEND, NORTH ARROW, SCALE BAR AND MAP INFORMATION
# ============================================================

PANEL_X = 210
PANEL_WIDTH = 77

DARK = QColor("#000000")
MUTED = QColor("#767676")


def add_label(text, x, y, width, height,
              size=8, bold=False, color=DARK,
              alignment=None):

    label = QgsLayoutItemLabel(layout)
    label.setText(text)

    font = QFont("Arial")
    font.setBold(bold)

    text_format = QgsTextFormat()
    text_format.setFont(font)
    text_format.setSize(size)
    text_format.setColor(color)

    label.setTextFormat(text_format)

    if alignment is not None:
        label.setHAlign(alignment)

    layout.addLayoutItem(label)
    label.attemptMove(QgsLayoutPoint(x, y))
    label.attemptResize(QgsLayoutSize(width, height))

    return label


def add_line(x, y, width):

    line = QgsLayoutItemShape(layout)
    line.setShapeType(QgsLayoutItemShape.Rectangle)

    symbol = QgsFillSymbol.createSimple({
        "color": "#767676",
        "outline_style": "no"
    })

    line.setSymbol(symbol)

    layout.addLayoutItem(line)
    line.attemptMove(QgsLayoutPoint(x, y))
    line.attemptResize(QgsLayoutSize(width, 0.35))

    return line


def add_swatch(x, y, color, width=9, height=4):

    swatch = QgsLayoutItemShape(layout)
    swatch.setShapeType(QgsLayoutItemShape.Rectangle)

    symbol = QgsFillSymbol.createSimple({
        "color": color,
        "outline_color": DARK.name(),
        "outline_width": "0.1"
    })

    swatch.setSymbol(symbol)

    layout.addLayoutItem(swatch)
    swatch.attemptMove(QgsLayoutPoint(x, y))
    swatch.attemptResize(QgsLayoutSize(width, height))

    return swatch


# ------------------------------------------------------------
# TITLE
# ------------------------------------------------------------

add_label(
    f"{MODEL.upper()} ASPECT MAP",
    PANEL_X, 12,
    PANEL_WIDTH, 12,
    size=19,
    bold=True
)

add_label(
    f"UAV-DERIVED {MODEL_NAME.upper()}",
    PANEL_X, 24,
    PANEL_WIDTH, 6,
    size=8,
    color=MUTED
)

add_line(PANEL_X, 32, PANEL_WIDTH)


# ------------------------------------------------------------
# LEGEND
# ------------------------------------------------------------

add_label(
    "LEGEND",
    PANEL_X, 36,
    PANEL_WIDTH, 7,
    size=11,
    bold=True
)

add_label(
    "SLOPE DIRECTION",
    PANEL_X, 45,
    PANEL_WIDTH, 5,
    size=8,
    bold=True,
    color=MUTED
)

# Representative directions from the continuous color ramp.

legend_directions = [
    (0,   "#2C7BB6", "North (0°)"),
    (45,  "#00A6CA", "Northeast (45°)"),
    (90,  "#00CC96", "East (90°)"),
    (135, "#A8D95B", "Southeast (135°)"),
    (180, "#F9D057", "South (180°)"),
    (225, "#F29E4C", "Southwest (225°)"),
    (270, "#E65C5C", "West (270°)"),
    (315, "#A66CC5", "Northwest (315°)")
]

y = 53

for angle, color, label in legend_directions:

    add_swatch(
        PANEL_X, y,
        color,
        9, 4
    )

    add_label(
        label,
        PANEL_X + 13, y - 0.5,
        PANEL_WIDTH - 13, 6,
        size=8
    )

    y += 8

add_line(PANEL_X, 120, PANEL_WIDTH)


# ------------------------------------------------------------
# MAP INFORMATION
# ------------------------------------------------------------

add_label(
    "MAP INFORMATION",
    PANEL_X, 125,
    PANEL_WIDTH, 6,
    size=8,
    bold=True
)

add_label(
    "Coordinate system:",
    PANEL_X, 133, 30, 5,
    size=7, bold=True
)

add_label(
    aspect_layer.crs().authid(),
    PANEL_X + 30, 133,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Aspect range:",
    PANEL_X, 139, 30, 5,
    size=7, bold=True
)

add_label(
    "0° – 360°",
    PANEL_X + 30, 139,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Resolution:",
    PANEL_X, 145, 30, 5,
    size=7, bold=True
)

add_label(
    f"{resolution_x:.2f} x {resolution_y:.2f} m",
    PANEL_X + 30, 145,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Survey area:",
    PANEL_X, 151, 30, 5,
    size=7, bold=True
)

add_label(
    f"{survey_area_ha:.2f} ha",
    PANEL_X + 30, 151,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Aspect coverage:",
    PANEL_X, 157, 30, 5,
    size=7, bold=True
)

add_label(
    f"{aspect_coverage:.1f}%",
    PANEL_X + 30, 157,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Aspect area:",
    PANEL_X, 163, 30, 5,
    size=7, bold=True
)

add_label(
    f"{aspect_area_m2:.1f} m²",
    PANEL_X + 30, 163,
    PANEL_WIDTH - 30, 5,
    size=7
)


# ============================================================
# NORTH ARROW
# ============================================================

north_arrow = QgsLayoutItemPicture(layout)
north_arrow.setPicturePath(str(NORTH_ARROW_PATH))

layout.addLayoutItem(north_arrow)

north_arrow.setResizeMode(QgsLayoutItemPicture.Zoom)

arrow_width = 30
arrow_height = 30

north_arrow.attemptResize(
    QgsLayoutSize(arrow_width, arrow_height)
)


def position_north_arrow(map_item, arrow, margin=3):

    map_pos = map_item.pos()
    map_size = map_item.sizeWithUnits()
    arrow_size = arrow.sizeWithUnits()

    x = (
        map_pos.x()
        + map_size.width()
        - arrow_size.width()
        - margin
    )

    y = (
        map_pos.y()
        + map_size.height()
        - arrow_size.height()
        - margin
    )

    arrow.attemptMove(QgsLayoutPoint(x, y))


position_north_arrow(map_item, north_arrow)
north_arrow.setZValue(10)


# ============================================================
# SCALE BAR
# ============================================================

target_segments = 4

map_extent = map_item.extent()
map_width_m = map_extent.width() * unit_to_meters

target_bar_distance = map_width_m * 0.25

raw_segment = target_bar_distance / target_segments
magnitude = 10 ** math.floor(math.log10(raw_segment))
normalized = raw_segment / magnitude

if normalized <= 1:
    nice_segment = 1
elif normalized <= 2:
    nice_segment = 2
elif normalized <= 5:
    nice_segment = 5
else:
    nice_segment = 10

segment_distance = nice_segment * magnitude

scale_bar = QgsLayoutItemScaleBar(layout)

scale_bar.setUnits(QgsUnitTypes.DistanceMeters)
scale_bar.setUnitsPerSegment(segment_distance)
scale_bar.setNumberOfSegments(target_segments)
scale_bar.setStyle("Single Box")
scale_bar.setLinkedMap(map_item)
scale_bar.setNumberOfSegmentsLeft(0)
scale_bar.setUnitLabel("m")
scale_bar.setHeight(2)

scale_text_format = QgsTextFormat()
scale_text_format.setFont(QFont("Arial"))
scale_text_format.setSize(7)
scale_text_format.setColor(DARK)

scale_bar.setTextFormat(scale_text_format)

fill_symbol = QgsFillSymbol.createSimple({
    "color": "#000000",
    "outline_color": "#000000",
    "outline_width": "0.2"
})

alternate_fill_symbol = QgsFillSymbol.createSimple({
    "color": "#FFFFFF",
    "outline_color": "#000000",
    "outline_width": "0.2"
})

scale_bar.setFillSymbol(fill_symbol)
scale_bar.setAlternateFillSymbol(alternate_fill_symbol)

line_symbol = QgsLineSymbol.createSimple({
    "line_color": "#000000",
    "line_width": "0.2"
})

scale_bar.setLineSymbol(line_symbol)

layout.addLayoutItem(scale_bar)


def position_scale_bar(map_item, scale_bar, margin=3):

    map_pos = map_item.pos()
    map_size = map_item.sizeWithUnits()
    scale_size = scale_bar.sizeWithUnits()

    x = map_pos.x() + margin

    y = (
        map_pos.y()
        + map_size.height()
        - scale_size.height()
        - margin
        + 4
    )

    scale_bar.attemptMove(QgsLayoutPoint(x, y))


position_scale_bar(map_item, scale_bar)


# ============================================================
# EXPORT PNG
# ============================================================

print("\nExporting PNG...")

if OUTPUT_PATH.exists():
    OUTPUT_PATH.unlink()

exporter = QgsLayoutExporter(layout)

settings = QgsLayoutExporter.ImageExportSettings()
settings.dpi = EXPORT_DPI

export_result = exporter.exportToImage(
    str(OUTPUT_PATH),
    settings
)

if export_result != QgsLayoutExporter.Success:
    raise RuntimeError(
        f"PNG export failed. Error code: {export_result}"
    )

print("Export successful:", OUTPUT_PATH)


# ============================================================
# CLEANUP
# ============================================================

dsm_src = None
aspect_src = None

qgs.exitQgis()

print("\nQGIS shutdown complete")
