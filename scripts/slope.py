
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
SLOPE_PATH = BASE_DIR.parent / "output" / "qgis" / "temp" / "slope.tif"
OUTPUT_PATH = BASE_DIR.parent / "output" / "qgis" / f"{MODEL}" / "slope_map.png"

QGIS_PREFIX = r"D:\QGis\apps\qgis"

PAGE_WIDTH = 297
PAGE_HEIGHT = 210

MAP_X = 10
MAP_Y = 10
MAP_WIDTH = 180
MAP_HEIGHT = 190

EXPORT_DPI = 300

NORTH_ARROW_PATH = BASE_DIR / "north_arrow.jpg"

# ============================================================
# INITIALIZE QGIS
# ============================================================

print("Initializing QGIS...")

QgsApplication.setPrefixPath(QGIS_PREFIX, True)

qgs = QgsApplication([], False)
qgs.initQgis()

gdal.UseExceptions()

print("QGIS initialized")


# ============================================================
# LOAD DSM
# ============================================================

if not DSM_PATH.exists():
    raise FileNotFoundError(f"DSM not found: {DSM_PATH}")

src = gdal.Open(str(DSM_PATH), gdal.GA_ReadOnly)

if src is None:
    raise RuntimeError("GDAL could not open DSM")

band = src.GetRasterBand(1)

arr = band.ReadAsArray()
nodata = band.GetNoDataValue()

valid_mask = np.isfinite(arr)

if nodata is not None:
    valid_mask &= arr != nodata

valid = arr[valid_mask]

if valid.size == 0:
    raise RuntimeError("DSM contains no valid pixels")

minimum = float(valid.min())
maximum = float(valid.max())

print("\n--- DSM INFORMATION ---")
print("Dimensions:", src.RasterXSize, "x", src.RasterYSize)
print("NoData:", nodata)
print("Minimum elevation:", minimum)
print("Maximum elevation:", maximum)


# ============================================================
# DETERMINE HORIZONTAL UNIT CONVERSION
# ============================================================

projection = src.GetProjection()

if not projection:
    raise RuntimeError(
        "DSM has no CRS. Assign its correct projected CRS "
        "before calculating slope."
    )

srs = osr.SpatialReference()
srs.ImportFromWkt(projection)

if srs.IsGeographic():
    raise RuntimeError(
        "DSM uses a geographic CRS. Reproject it into a "
        "projected CRS before calculating slope."
    )

unit_to_meters = srs.GetLinearUnits()

if unit_to_meters <= 0:
    raise RuntimeError("Invalid horizontal CRS units")

# Elevation values are assumed to be in metres.
# GDAL scale converts horizontal CRS units to elevation units.
scale_factor = 1.0 / unit_to_meters

print("Horizontal unit conversion:", unit_to_meters)
print("GDAL slope scale:", scale_factor)


# ============================================================
# GENERATE SLOPE RASTER
# ============================================================

print("\nGenerating slope raster...")

SLOPE_PATH.parent.mkdir(parents=True, exist_ok=True)

if SLOPE_PATH.exists():
    SLOPE_PATH.unlink()

slope_options = gdal.DEMProcessingOptions(
    slopeFormat="degree",
    scale=scale_factor,
    computeEdges=True,
    creationOptions=[
        "COMPRESS=LZW",
        "TILED=YES"
    ]
)

slope_ds = gdal.DEMProcessing(
    str(SLOPE_PATH),
    src,
    "slope",
    options=slope_options
)

if slope_ds is None:
    raise RuntimeError("Failed to generate slope raster")

slope_ds = None

print("Slope raster generated:", SLOPE_PATH)


# ============================================================
# LOAD SLOPE RASTER
# ============================================================

project = QgsProject.instance()
project.clear()

slope_layer = QgsRasterLayer(
    str(SLOPE_PATH),
    "Slope (degrees)"
)

if not slope_layer.isValid():
    raise RuntimeError("Failed to load slope raster")

project.addMapLayer(slope_layer)
project.setCrs(slope_layer.crs())

slope_src = gdal.Open(str(SLOPE_PATH), gdal.GA_ReadOnly)

if slope_src is None:
    raise RuntimeError("Could not reopen slope raster")

slope_band = slope_src.GetRasterBand(1)

slope_arr = slope_band.ReadAsArray()
slope_nodata = slope_band.GetNoDataValue()

slope_valid_mask = np.isfinite(slope_arr)

if slope_nodata is not None:
    slope_valid_mask &= slope_arr != slope_nodata

slope_valid = slope_arr[slope_valid_mask]

if slope_valid.size == 0:
    raise RuntimeError("Slope raster contains no valid pixels")

minimum_slope = float(slope_valid.min())
maximum_slope = float(slope_valid.max())
mean_slope = float(np.mean(slope_valid))

print("\n--- SLOPE STATISTICS ---")
print("Minimum slope:", minimum_slope)
print("Maximum slope:", maximum_slope)
print("Mean slope:", mean_slope)


# ============================================================
# RASTER RESOLUTION AND SURVEY AREA
# ============================================================

geotransform = src.GetGeoTransform()

pixel_width = math.hypot(
    geotransform[1],
    geotransform[4]
)

pixel_height = math.hypot(
    geotransform[2],
    geotransform[5]
)

resolution_x = pixel_width * unit_to_meters
resolution_y = pixel_height * unit_to_meters

pixel_area = abs(
    geotransform[1] * geotransform[5]
    - geotransform[2] * geotransform[4]
) * unit_to_meters ** 2

survey_area_m2 = slope_valid.size * pixel_area
survey_area_ha = survey_area_m2 / 10000

print("Resolution:", resolution_x, "x", resolution_y, "m")
print("Survey area:", survey_area_m2, "m²")
print("Survey area:", survey_area_ha, "ha")


# ============================================================
# STYLE SLOPE RASTER
# ============================================================

print("\nStyling slope raster...")

# Slope classes in degrees.
# Each color represents the interval beginning at its value.

slope_classes = [
    (0, 5, "#2C7BB6", "Flat"),
    (5, 15, "#ABD9E9", "Gentle"),
    (15, 30, "#FFFFBF", "Moderate"),
    (30, 45, "#FDAE61", "Steep"),
    (45, 60, "#F46D43", "Very steep"),
    (60, 90, "#A50026", "Extreme")
]

shader = QgsColorRampShader()
shader.setColorRampType(QgsColorRampShader.Discrete)

ramp_items = [
    QgsColorRampShader.ColorRampItem(
        lower,
        QColor(color),
        f"{lower}–{upper}°"
    )
    for lower, upper, color, label in slope_classes
]

ramp_items.append(
    QgsColorRampShader.ColorRampItem(
        90,
        QColor("#A50026"),
        "90°"
    )
)

shader.setColorRampItemList(ramp_items)

# Continuous slope color ramp
shader = QgsRasterShader()
shader_function = QgsColorRampShader()

shader_function.setColorRampType(QgsColorRampShader.Interpolated)
shader_function.setColorRampItemList([
    QgsColorRampShader.ColorRampItem(0,  QColor("#2C7BB6"), "0°"),
    QgsColorRampShader.ColorRampItem(5,  QColor("#ABD9E9"), "5°"),
    QgsColorRampShader.ColorRampItem(15, QColor("#FFFFBF"), "15°"),
    QgsColorRampShader.ColorRampItem(30, QColor("#FDAE61"), "30°"),
    QgsColorRampShader.ColorRampItem(45, QColor("#F46D43"), "45°"),
    QgsColorRampShader.ColorRampItem(60, QColor("#A50026"), "60°"),
    QgsColorRampShader.ColorRampItem(90, QColor("#A50026"), "90°"),
])

shader.setRasterShaderFunction(shader_function)

renderer = QgsSingleBandPseudoColorRenderer(
    slope_layer.dataProvider(),
    1,
    shader
)

slope_layer.setRenderer(renderer)
slope_layer.triggerRepaint()


# ============================================================
# CREATE PRINT LAYOUT
# ============================================================

print("\nCreating layout...")

layout = QgsPrintLayout(project)
layout.initializeDefaults()

layout.setName("Slope Map")

page = layout.pageCollection().pages()[0]

page.setPageSize(
    QgsLayoutSize(PAGE_WIDTH, PAGE_HEIGHT)
)

map_item = QgsLayoutItemMap(layout)

layout.addLayoutItem(map_item)

map_item.setLayers([slope_layer])
map_item.setKeepLayerSet(True)

map_item.attemptMove(
    QgsLayoutPoint(MAP_X, MAP_Y)
)

map_item.attemptResize(
    QgsLayoutSize(MAP_WIDTH, MAP_HEIGHT)
)

extent = slope_layer.extent()
extent.scale(1.15)

map_item.zoomToExtent(extent)
map_item.setFrameEnabled(True)

project.layoutManager().addLayout(layout)


# ============================================================
# GEOGRAPHIC COORDINATE GRID
# ============================================================

def calculate_grid_interval(map_item, target_x=4, target_y=6):

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

    transformed = [
        transform.transform(point)
        for point in corners
    ]

    lon_range = (
        max(p.x() for p in transformed)
        - min(p.x() for p in transformed)
    )

    lat_range = (
        max(p.y() for p in transformed)
        - min(p.y() for p in transformed)
    )

    def nice_interval(value):

        if value <= 0:
            raise ValueError("Invalid geographic grid extent")

        exponent = math.floor(math.log10(value))
        magnitude = 10 ** exponent

        candidates = [
            1 * magnitude,
            2 * magnitude,
            5 * magnitude,
            10 * magnitude
        ]

        return min(
            candidates,
            key=lambda x: abs(x - value)
        )

    lon_interval = nice_interval(lon_range / target_x)
    lat_interval = nice_interval(lat_range / target_y)

    print("Longitude interval:", lon_interval)
    print("Latitude interval:", lat_interval)

    return lon_interval, lat_interval


grid = QgsLayoutItemMapGrid(
    "Geographic Coordinates",
    map_item
)

map_item.grids().addGrid(grid)

grid.setCrs(
    QgsCoordinateReferenceSystem("EPSG:4326")
)

lon_interval, lat_interval = calculate_grid_interval(
    map_item,
    target_x=4,
    target_y=6
)

grid.setIntervalX(lon_interval)
grid.setIntervalY(lat_interval)

grid.setStyle(
    QgsLayoutItemMapGrid.FrameAnnotationsOnly
)

grid.setFrameStyle(
    QgsLayoutItemMapGrid.ExteriorTicks
)

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


def add_label(
    text, x, y, width, height,
    size=8, bold=False, color=DARK,
    alignment=None
):

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

    label.attemptMove(
        QgsLayoutPoint(x, y)
    )

    label.attemptResize(
        QgsLayoutSize(width, height)
    )

    return label


def add_line(x, y, width):

    line = QgsLayoutItemShape(layout)

    line.setShapeType(
        QgsLayoutItemShape.Rectangle
    )

    symbol = QgsFillSymbol.createSimple({
        "color": "#767676",
        "outline_style": "no"
    })

    line.setSymbol(symbol)

    layout.addLayoutItem(line)

    line.attemptMove(
        QgsLayoutPoint(x, y)
    )

    line.attemptResize(
        QgsLayoutSize(width, 0.35)
    )

    return line


def add_swatch(x, y, color, width=9, height=4):

    swatch = QgsLayoutItemShape(layout)

    swatch.setShapeType(
        QgsLayoutItemShape.Rectangle
    )

    symbol = QgsFillSymbol.createSimple({
        "color": color,
        "outline_color": DARK.name(),
        "outline_width": "0.1"
    })

    swatch.setSymbol(symbol)

    layout.addLayoutItem(swatch)

    swatch.attemptMove(
        QgsLayoutPoint(x, y)
    )

    swatch.attemptResize(
        QgsLayoutSize(width, height)
    )

    return swatch


# ------------------------------------------------------------
# TITLE
# ------------------------------------------------------------

add_label(
    f"{MODEL.upper()} SLOPE MAP",
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
    "SLOPE (°)",
    PANEL_X, 45,
    PANEL_WIDTH, 5,
    size=8,
    bold=True,
    color=MUTED
)


# ------------------------------------------------------------
# SLOPE CLASS LEGEND
# ------------------------------------------------------------

legend_y = 53

for lower, upper, color, name in slope_classes:

    # Skip classes completely outside the actual data range.
    if lower > maximum_slope:
        continue

    add_swatch(
        PANEL_X,
        legend_y,
        color,
        9,
        4
    )

    add_label(
        f"{lower}–{upper}°  {name}",
        PANEL_X + 13,
        legend_y + 1,
        PANEL_WIDTH - 13,
        6,
        size=8
    )

    legend_y += 8


# ------------------------------------------------------------
# MAP INFORMATION
# ------------------------------------------------------------

add_line(PANEL_X, 109, PANEL_WIDTH)

add_label(
    "MAP INFORMATION",
    PANEL_X, 114,
    PANEL_WIDTH, 6,
    size=8,
    bold=True
)

add_label(
    "Coordinate system:",
    PANEL_X, 122,
    30, 5,
    size=7,
    bold=True
)

add_label(
    slope_layer.crs().authid(),
    PANEL_X + 30, 122,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Slope range:",
    PANEL_X, 128,
    30, 5,
    size=7,
    bold=True
)

add_label(
    f"{minimum_slope:.2f} – {maximum_slope:.2f}°",
    PANEL_X + 30, 128,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Mean slope:",
    PANEL_X, 134,
    30, 5,
    size=7,
    bold=True
)

add_label(
    f"{mean_slope:.2f}°",
    PANEL_X + 30, 134,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Resolution:",
    PANEL_X, 140,
    30, 5,
    size=7,
    bold=True
)

add_label(
    f"{resolution_x:.2f} x {resolution_y:.2f} m",
    PANEL_X + 30, 140,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Survey area:",
    PANEL_X, 146,
    30, 5,
    size=7,
    bold=True
)

add_label(
    f"{survey_area_ha:.2f} ha",
    PANEL_X + 30, 146,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Raster size:",
    PANEL_X, 152,
    30, 5,
    size=7,
    bold=True
)

add_label(
    f"{slope_src.RasterXSize} x {slope_src.RasterYSize}",
    PANEL_X + 30, 152,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Slope units:",
    PANEL_X, 158,
    30, 5,
    size=7,
    bold=True
)

add_label(
    "Degrees",
    PANEL_X + 30, 158,
    PANEL_WIDTH - 30, 5,
    size=7
)


# ============================================================
# NORTH ARROW
# ============================================================

north_arrow = QgsLayoutItemPicture(layout)

north_arrow.setPicturePath(
    str(NORTH_ARROW_PATH)
)

layout.addLayoutItem(north_arrow)

north_arrow.setResizeMode(
    QgsLayoutItemPicture.Zoom
)

arrow_width = 30
arrow_height = 30

north_arrow.attemptResize(
    QgsLayoutSize(
        arrow_width,
        arrow_height
    )
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

    arrow.attemptMove(
        QgsLayoutPoint(x, y)
    )


position_north_arrow(
    map_item,
    north_arrow
)


# ============================================================
# DYNAMIC SCALE BAR
# ============================================================

target_segments = 4

map_extent = map_item.extent()

map_width_m = (
    map_extent.width()
    * unit_to_meters
)

target_bar_distance = map_width_m * 0.25

raw_segment = target_bar_distance / target_segments

if raw_segment <= 0:
    raise RuntimeError("Invalid scale bar segment distance")

magnitude = 10 ** math.floor(
    math.log10(raw_segment)
)

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

print("Scale bar segment:", segment_distance, "m")

scale_bar = QgsLayoutItemScaleBar(layout)

scale_bar.setUnits(
    QgsUnitTypes.DistanceMeters
)

scale_bar.setUnitsPerSegment(
    segment_distance
)

scale_bar.setNumberOfSegments(
    target_segments
)

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
scale_bar.setAlternateFillSymbol(
    alternate_fill_symbol
)

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

    scale_bar.attemptMove(
        QgsLayoutPoint(x, y)
    )


position_scale_bar(
    map_item,
    scale_bar
)


# ============================================================
# EXPORT PNG
# ============================================================

print("\nExporting PNG...")

if OUTPUT_PATH.exists():
    OUTPUT_PATH.unlink()

exporter = QgsLayoutExporter(layout)

settings = QgsLayoutExporter.ImageExportSettings()
settings.dpi = EXPORT_DPI

result = exporter.exportToImage(
    str(OUTPUT_PATH),
    settings
)

if result != QgsLayoutExporter.Success:
    raise RuntimeError(
        f"PNG export failed. Error code: {result}"
    )

print("Export successful:", OUTPUT_PATH)


# ============================================================
# CLEANUP
# ============================================================

slope_src = None
src = None

qgs.exitQgis()

print("\nQGIS shutdown complete")
