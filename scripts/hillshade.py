
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
HILLSHADE_PATH =  BASE_DIR.parent / "output" / "qgis" / "temp" / "hillshade.tif"
OUTPUT_PATH = BASE_DIR.parent / "output" / "qgis" / f"{MODEL}" / "hillshade_map.png"

NORTH_ARROW_PATH = BASE_DIR / "north_arrow.jpg"

QGIS_PREFIX = r"D:\QGis\apps\qgis"

PAGE_WIDTH = 297
PAGE_HEIGHT = 210

MAP_X = 10
MAP_Y = 10
MAP_WIDTH = 180
MAP_HEIGHT = 190

EXPORT_DPI = 300

# Hillshade illumination parameters
SUN_AZIMUTH = 315
SUN_ALTITUDE = 45
Z_FACTOR = 1.0
VERTICAL_SCALE = 1.0


# ============================================================
# INITIALIZE QGIS
# ============================================================

print("Initializing QGIS...")

QgsApplication.setPrefixPath(QGIS_PREFIX, True)

qgs = QgsApplication([], False)
qgs.initQgis()

print("QGIS initialized")


# ============================================================
# LOAD DSM
# ============================================================

if not DSM_PATH.exists():
    raise FileNotFoundError(f"DSM not found: {DSM_PATH}")

project = QgsProject.instance()
project.clear()

dsm = QgsRasterLayer(str(DSM_PATH), "Digital Surface Model")

if not dsm.isValid():
    raise RuntimeError(f"Failed to load DSM: {DSM_PATH}")

project.addMapLayer(dsm)
project.setCrs(dsm.crs())

if not dsm.crs().isValid():
    raise RuntimeError("DSM has no valid CRS")

if dsm.crs().mapUnits() == QgsUnitTypes.DistanceDegrees:
    raise RuntimeError(
        "A projected CRS is required for accurate hillshade calculations."
    )

print("DSM layer loaded")
print("CRS:", dsm.crs().authid())


# ============================================================
# GENERATE HILLSHADE
# ============================================================

print("\nGenerating hillshade raster...")

if HILLSHADE_PATH.exists():
    HILLSHADE_PATH.unlink()

src = gdal.Open(str(DSM_PATH), gdal.GA_ReadOnly)

if src is None:
    raise RuntimeError("GDAL could not open DSM")

gdal.UseExceptions()

options = gdal.DEMProcessingOptions(
    format="GTiff",
    creationOptions=[
        "COMPRESS=LZW",
        "TILED=YES"
    ],
    azimuth=SUN_AZIMUTH,
    altitude=SUN_ALTITUDE,
    zFactor=Z_FACTOR,
    scale=VERTICAL_SCALE,
    computeEdges=True,
    alg="Horn"
)

gdal.DEMProcessing(
    str(HILLSHADE_PATH),
    src,
    "hillshade",
    options=options
)

print("Hillshade generated:", HILLSHADE_PATH)


# ============================================================
# LOAD HILLSHADE
# ============================================================

hillshade = QgsRasterLayer(
    str(HILLSHADE_PATH),
    "Hillshade"
)

if not hillshade.isValid():
    raise RuntimeError("Failed to load hillshade raster")

project.addMapLayer(hillshade)

print("Hillshade layer loaded")


# ============================================================
# INSPECT HILLSHADE VALUES
# ============================================================

print("\n--- HILLSHADE DEBUG ---")

shade_ds = gdal.Open(str(HILLSHADE_PATH), gdal.GA_ReadOnly)

if shade_ds is None:
    raise RuntimeError("Could not open generated hillshade")

shade_band = shade_ds.GetRasterBand(1)
shade_arr = shade_band.ReadAsArray()
shade_nodata = shade_band.GetNoDataValue()

shade_mask = np.isfinite(shade_arr)

if shade_nodata is not None:
    shade_mask &= shade_arr != shade_nodata

shade_valid = shade_arr[shade_mask]

if shade_valid.size == 0:
    raise RuntimeError("Hillshade contains no valid pixels")

shade_min = float(shade_valid.min())
shade_max = float(shade_valid.max())
shade_mean = float(shade_valid.mean())

print("Dimensions:", shade_ds.RasterXSize, "x", shade_ds.RasterYSize)
print("NoData:", shade_nodata)
print("Minimum illumination:", shade_min)
print("Maximum illumination:", shade_max)
print("Mean illumination:", shade_mean)
print("Valid pixels:", shade_valid.size)


# ============================================================
# STYLE HILLSHADE
# ============================================================

print("\nStyling hillshade...")

provider = hillshade.dataProvider()

shader = QgsColorRampShader()
shader.setColorRampType(QgsColorRampShader.Interpolated)

items = [
    QgsColorRampShader.ColorRampItem(0,   QColor("#230036"), "0 - Very dark"),
    QgsColorRampShader.ColorRampItem(51,  QColor("#421454"), "51 - Dark"),
    QgsColorRampShader.ColorRampItem(102, QColor("#802C62"), "102 - Low"),
    QgsColorRampShader.ColorRampItem(153, QColor("#FF5B42"), "153 - Medium"),
    QgsColorRampShader.ColorRampItem(204, QColor("#FFD749"), "204 - High"),
    QgsColorRampShader.ColorRampItem(255, QColor("#FFFFCA"), "255 - Very bright")
]

shader.setColorRampItemList(items)

raster_shader = QgsRasterShader()
raster_shader.setRasterShaderFunction(shader)

renderer = QgsSingleBandPseudoColorRenderer(
    provider,
    1,
    raster_shader
)

renderer.setClassificationMin(0)
renderer.setClassificationMax(255)

hillshade.setRenderer(renderer)
hillshade.triggerRepaint()

print("Hillshade styled")


# ============================================================
# CREATE PRINT LAYOUT
# ============================================================

print("\nCreating layout...")

layout = QgsPrintLayout(project)
layout.initializeDefaults()

layout.setName("Hillshade Map")

page = layout.pageCollection().pages()[0]

page.setPageSize(
    QgsLayoutSize(PAGE_WIDTH, PAGE_HEIGHT)
)

map_item = QgsLayoutItemMap(layout)

layout.addLayoutItem(map_item)

map_item.setLayers([hillshade])
map_item.setKeepLayerSet(True)

map_item.attemptMove(
    QgsLayoutPoint(MAP_X, MAP_Y)
)

map_item.attemptResize(
    QgsLayoutSize(MAP_WIDTH, MAP_HEIGHT)
)

extent = hillshade.extent()
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

grid.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))

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
# LAYOUT HELPERS
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
        "outline_color": "#000000",
        "outline_width": "0.1"
    })

    swatch.setSymbol(symbol)

    layout.addLayoutItem(swatch)

    swatch.attemptMove(QgsLayoutPoint(x, y))
    swatch.attemptResize(QgsLayoutSize(width, height))

    return swatch


# ============================================================
# TITLE
# ============================================================

add_label(
    f"{MODEL.upper()} HILLSHADE MAP",
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


# ============================================================
# LEGEND
# ============================================================

add_label(
    "LEGEND",
    PANEL_X, 36,
    PANEL_WIDTH, 7,
    size=11,
    bold=True
)

add_label(
    "ILLUMINATION",
    PANEL_X, 45,
    PANEL_WIDTH, 5,
    size=8,
    bold=True,
    color=MUTED
)

legend_items = [
    (0,   "#230036", "0 - Very dark"),
    (51,  "#421454", "51 - Dark"),
    (102, "#802C62", "102 - Low"),
    (153, "#FF5B42", "153 - Medium"),
    (204, "#FFD749", "204 - High"),
    (255, "#FFFFCA", "255 - Very bright")
]

y = 53

for value, color, label in legend_items:

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

    y += 9

add_line(PANEL_X, 106, PANEL_WIDTH)


# ============================================================
# DSM AND HILLSHADE STATISTICS
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

srs = osr.SpatialReference()

if src.GetProjection():
    srs.ImportFromWkt(src.GetProjection())
    unit_to_meters = srs.GetLinearUnits()
else:
    raise RuntimeError(
        "DSM projection is missing; cannot calculate map measurements."
    )

resolution_x = pixel_width * unit_to_meters
resolution_y = pixel_height * unit_to_meters

pixel_area = abs(
    geotransform[1] * geotransform[5]
    - geotransform[2] * geotransform[4]
) * unit_to_meters ** 2

survey_area_m2 = shade_valid.size * pixel_area
survey_area_ha = survey_area_m2 / 10000

# Original DSM elevation statistics
dsm_band = src.GetRasterBand(1)
dsm_arr = dsm_band.ReadAsArray()
dsm_nodata = dsm_band.GetNoDataValue()

dsm_mask = np.isfinite(dsm_arr)

if dsm_nodata is not None:
    dsm_mask &= dsm_arr != dsm_nodata

dsm_valid = dsm_arr[dsm_mask]

if dsm_valid.size == 0:
    raise RuntimeError("DSM contains no valid elevation pixels")

elevation_min = float(dsm_valid.min())
elevation_max = float(dsm_valid.max())
mean_elevation = float(dsm_valid.mean())

print("\n--- MAP STATISTICS ---")
print(f"Mean elevation: {mean_elevation:.2f} m")
print(f"Raster resolution: {resolution_x:.3f} x {resolution_y:.3f} m")
print(f"Survey area: {survey_area_m2:.2f} m²")
print(f"Survey area: {survey_area_ha:.4f} ha")
print(f"Mean illumination: {shade_mean:.2f}")


# ============================================================
# MAP INFORMATION
# ============================================================

add_label(
    "MAP INFORMATION",
    PANEL_X, 110,
    PANEL_WIDTH, 6,
    size=8,
    bold=True
)

add_label(
    "Coordinate system:",
    PANEL_X, 117, 30, 5,
    size=7, bold=True
)

add_label(
    dsm.crs().authid(),
    PANEL_X + 30, 117,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Sun azimuth:",
    PANEL_X, 123, 30, 5,
    size=7, bold=True
)

add_label(
    f"{SUN_AZIMUTH}°",
    PANEL_X + 30, 123,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Sun altitude:",
    PANEL_X, 129, 30, 5,
    size=7, bold=True
)

add_label(
    f"{SUN_ALTITUDE}°",
    PANEL_X + 30, 129,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Mean elevation:",
    PANEL_X, 135, 30, 5,
    size=7, bold=True
)

add_label(
    f"{mean_elevation:.2f} m",
    PANEL_X + 30, 135,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Resolution:",
    PANEL_X, 141, 30, 5,
    size=7, bold=True
)

add_label(
    f"{resolution_x:.2f} x {resolution_y:.2f} m",
    PANEL_X + 30, 141,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Survey area:",
    PANEL_X, 147, 30, 5,
    size=7, bold=True
)

add_label(
    f"{survey_area_ha:.2f} ha",
    PANEL_X + 30, 147,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Illumination:",
    PANEL_X, 153, 30, 5,
    size=7, bold=True
)

add_label(
    f"{shade_min:.0f} – {shade_max:.0f}",
    PANEL_X + 30, 153,
    PANEL_WIDTH - 30, 5,
    size=7
)

add_label(
    "Mean illumination:",
    PANEL_X, 159, 30, 5,
    size=7, bold=True
)

add_label(
    f"{shade_mean:.2f}",
    PANEL_X + 30, 159,
    PANEL_WIDTH - 30, 5,
    size=7
)


# ============================================================
# NORTH ARROW
# ============================================================

if not NORTH_ARROW_PATH.exists():
    raise FileNotFoundError(
        f"North arrow image not found: {NORTH_ARROW_PATH}"
    )

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
    arrow.setZValue(10)


position_north_arrow(map_item, north_arrow)


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

src = None
shade_ds = None

qgs.exitQgis()

print("\nQGIS shutdown complete")
