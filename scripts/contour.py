import os, sys, math
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr, osr

from qgis.core import (
    QgsApplication,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsLineSymbol,
    QgsPrintLayout,
    QgsLayoutItemMap,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsLayoutExporter,
    QgsSingleBandPseudoColorRenderer,
    QgsColorRampShader,
    QgsRasterShader,
    QgsPalLayerSettings,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsVectorLayerSimpleLabeling,
    QgsCoordinateReferenceSystem,
    QgsLayoutItemMapGrid,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsLayoutItemLabel,
    QgsLayoutItemShape,
    QgsLayoutItemScaleBar,
    QgsUnitTypes,
    QgsFillSymbol,
    QgsLayoutItemPicture
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
OUTPUT_PATH = BASE_DIR.parent / "output" / "qgis" / f"{MODEL}" / "topographic_map.png"

MINOR_CONTOUR_PATH = BASE_DIR.parent / "output" / "qgis" / "temp" / "contours_minor.gpkg"
INDEX_CONTOUR_PATH = BASE_DIR.parent / "output" / "qgis" / "temp" / "contours_index.gpkg"

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
# LOAD DSM
# ============================================================

if not DSM_PATH.exists():
    raise FileNotFoundError(f"DSM not found: {DSM_PATH}")

project = QgsProject.instance()
project.clear()

dsm = QgsRasterLayer(str(DSM_PATH), MODEL_NAME)

if not dsm.isValid():
    raise RuntimeError(f"Failed to load DSM: {DSM_PATH}")

project.addMapLayer(dsm)
project.setCrs(dsm.crs())

print("DSM layer added")


# ============================================================
# INSPECT DSM VALUES
# ============================================================

print("\n--- DSM DEBUG ---")

src = gdal.Open(str(DSM_PATH), gdal.GA_ReadOnly)

if src is None:
    raise RuntimeError("GDAL could not open DSM")

band = src.GetRasterBand(1)

arr = band.ReadAsArray()
nodata = band.GetNoDataValue()

valid_mask = np.isfinite(arr)

if nodata is not None:
    valid_mask &= (arr != nodata)

valid = arr[valid_mask]

print("Dimensions:", src.RasterXSize, "x", src.RasterYSize)
print("CRS:", dsm.crs().authid())
print("NoData:", nodata)
print("Data type:", gdal.GetDataTypeName(band.DataType))
print("Total pixels:", arr.size)
print("Valid pixels:", valid.size)

if valid.size == 0:
    raise RuntimeError("DSM contains no valid elevation pixels")

minimum = float(valid.min())
maximum = float(valid.max())
elevation_range = maximum - minimum

print("Minimum elevation:", minimum)
print("Maximum elevation:", maximum)
print("Elevation range:", elevation_range)
print("Unique values:", np.unique(valid).size)


# ============================================================
# DYNAMIC CONTOUR INTERVALS
# ============================================================

def calculate_contour_intervals(elevation_range):
    # Aim for roughly 6 index contours
    target_count = 6

    raw_interval = elevation_range / target_count

    magnitude = 10 ** math.floor(math.log10(raw_interval))
    normalized = raw_interval / magnitude

    if normalized <= 1:
        nice_interval = 1
    elif normalized <= 2:
        nice_interval = 2
    elif normalized <= 2.5:
        nice_interval = 2.5
    elif normalized <= 5:
        nice_interval = 5
    else:
        nice_interval = 10

    index_interval = nice_interval * magnitude
    minor_interval = index_interval / 3

    return minor_interval, index_interval


MINOR_INTERVAL, INDEX_INTERVAL = calculate_contour_intervals(
    elevation_range
)

print("\n--- CONTOUR INTERVALS ---")
print(f"Minor contour interval: {MINOR_INTERVAL:g} m")
print(f"Index contour interval: {INDEX_INTERVAL:g} m")


# ============================================================
# STYLE DSM
# ============================================================

print("\nStyling DSM...")

provider = dsm.dataProvider()

shader = QgsColorRampShader()
shader.setColorRampType(QgsColorRampShader.Interpolated)

items = [
    QgsColorRampShader.ColorRampItem(
        minimum, QColor("#36656B"), "Low"
    ),
    QgsColorRampShader.ColorRampItem(
        minimum + elevation_range / 3,
        QColor("#75B06F"), "Lower-middle"
    ),
    QgsColorRampShader.ColorRampItem(
        minimum + 2 * elevation_range / 3,
        QColor("#E0D679"), "Upper-middle"
    ),
    QgsColorRampShader.ColorRampItem(
        maximum, QColor("#FFF7CB"), "High"
    )
]

shader.setColorRampItemList(items)

raster_shader = QgsRasterShader()
raster_shader.setRasterShaderFunction(shader)

renderer = QgsSingleBandPseudoColorRenderer(
    provider,
    1,
    raster_shader
)

renderer.setClassificationMin(minimum)
renderer.setClassificationMax(maximum)

dsm.setRenderer(renderer)
dsm.triggerRepaint()

renderer = dsm.renderer()

print("Renderer type:", renderer.type())

if hasattr(renderer, "shader"):
    shader = renderer.shader()
    color_ramp = shader.rasterShaderFunction()

    if hasattr(color_ramp, "colorRampItemList"):
        for item in color_ramp.colorRampItemList():
            print(item.value, item.label, item.color.name())

print("DSM styled:", minimum, "-", maximum)


# ============================================================
# GENERATE CONTOURS USING GDAL DIRECTLY
# ============================================================

def generate_contours(interval, output_path, name):

    output_path = Path(output_path)

    print(f"\nGenerating {name} contours ({interval:g}m)...")

    if output_path.exists():
        output_path.unlink()

    driver = ogr.GetDriverByName("GPKG")
    ds = driver.CreateDataSource(str(output_path))

    if ds is None:
        raise RuntimeError("Could not create GeoPackage")

    srs = osr.SpatialReference()
    projection = src.GetProjection()

    if projection:
        srs.ImportFromWkt(projection)
    else:
        srs = None

    layer = ds.CreateLayer(
        "contours",
        srs=srs,
        geom_type=ogr.wkbLineString
    )

    field = ogr.FieldDefn("ELEV", ogr.OFTReal)
    layer.CreateField(field)

    first_level = math.ceil(minimum / interval) * interval
    last_level = math.floor(maximum / interval) * interval

    levels = [
        float(x)
        for x in np.arange(
            first_level,
            last_level + interval * 0.5,
            interval
        )
    ]

    print(f"Contour levels: {len(levels)}")
    print(f"Elevation range: {first_level:g} - {last_level:g} m")

    result = gdal.ContourGenerate(
        band,
        -1.0,
        0.0,
        levels,
        1,
        float(nodata) if nodata is not None else -9999.0,
        layer,
        -1,
        0
    )

    print("GDAL result:", result)
    print("Features before closing:", layer.GetFeatureCount())

    ds.FlushCache()
    ds = None

    contour_layer = QgsVectorLayer(
        str(output_path) + "|layername=contours",
        name,
        "ogr"
    )

    if not contour_layer.isValid():
        raise RuntimeError("Could not load generated contours")

    print("Features after reopening:", contour_layer.featureCount())

    return contour_layer


minor_contours = generate_contours(
    MINOR_INTERVAL,
    MINOR_CONTOUR_PATH,
    "Minor contours"
)

index_contours = generate_contours(
    INDEX_INTERVAL,
    INDEX_CONTOUR_PATH,
    "Index contours"
)


# ============================================================
# STYLE CONTOURS
# ============================================================

print("\nStyling contours...")

minor_symbol = QgsLineSymbol.createSimple({
    "line_color": "32,52,81,100",
    "line_width": "0.1",
    "line_width_unit": "MM"
})

minor_contours.renderer().setSymbol(minor_symbol)

index_symbol = QgsLineSymbol.createSimple({
    "line_color": "32,52,81,150",
    "line_width": "0.2",
    "line_width_unit": "MM"
})

index_contours.renderer().setSymbol(index_symbol)

print("Contour styling completed")

print("Minor contours:", minor_contours.featureCount())
print("Index contours:", index_contours.featureCount())


# ============================================================
# LABEL INDEX CONTOURS
# ============================================================

label_settings = QgsPalLayerSettings()

label_settings.fieldName = 'format_number("ELEV", 0)'
label_settings.isExpression = True

label_settings.placement = QgsPalLayerSettings.Line

font = QFont("Arial", 5)
font.setBold(True)

text_format = QgsTextFormat()
text_format.setFont(font)
text_format.setSize(5)
text_format.setColor(QColor("#203451"))

buffer = QgsTextBufferSettings()
buffer.setEnabled(True)
buffer.setSize(0.4)
buffer.setColor(QColor("white"))

text_format.setBuffer(buffer)

label_settings.setFormat(text_format)

index_contours.setLabeling(
    QgsVectorLayerSimpleLabeling(label_settings)
)

index_contours.setLabelsEnabled(True)

print("Index contour labels enabled")


# ============================================================
# CREATE PRINT LAYOUT
# ============================================================

print("\nCreating layout...")

layout = QgsPrintLayout(project)
layout.initializeDefaults()

layout.setName(f"{MODEL} TOPOGRAPHIC MAP",)

page = layout.pageCollection().pages()[0]

page.setPageSize(
    QgsLayoutSize(PAGE_WIDTH, PAGE_HEIGHT)
)

map_item = QgsLayoutItemMap(layout)

layout.addLayoutItem(map_item)

map_item.setLayers([
    index_contours,
    minor_contours,
    dsm
])

map_item.setKeepLayerSet(True)

map_item.attemptMove(
    QgsLayoutPoint(MAP_X, MAP_Y)
)

map_item.attemptResize(
    QgsLayoutSize(MAP_WIDTH, MAP_HEIGHT)
)

extent = dsm.extent()
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

    lon_range = max(p.x() for p in transformed) - min(p.x() for p in transformed)
    lat_range = max(p.y() for p in transformed) - min(p.y() for p in transformed)

    def nice_interval(value):

        exponent = math.floor(math.log10(value))
        magnitude = 10 ** exponent

        candidates = [
            1 * magnitude,
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
LIGHT = QColor("#FFFFFF")
PAPER = QColor("#FFFFFF")


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
    f"{MODEL.upper()} TOPOGRAPHIC MAP",
    PANEL_X, 12,
    PANEL_WIDTH, 12,
    size=17,
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
    "ELEVATION (m)",
    PANEL_X, 45,
    PANEL_WIDTH, 5,
    size=8,
    bold=True,
    color=MUTED
)


# ------------------------------------------------------------
# ELEVATION LEGEND
# ------------------------------------------------------------

renderer = dsm.renderer()
shader = renderer.shader()
shader_function = shader.rasterShaderFunction()

legend_items = shader_function.colorRampItemList()

y = 53

for item in legend_items:

    add_swatch(
        PANEL_X, y,
        item.color.name(),
        9, 4
    )

    add_label(
        f"{item.value:.2f} m",
        PANEL_X + 13, y + 1,
        PANEL_WIDTH - 13, 6,
        size=8
    )

    y += 9


# ------------------------------------------------------------
# CONTOUR FEATURES
# ------------------------------------------------------------

add_label(
    "CONTOURS",
    PANEL_X, 94,
    PANEL_WIDTH, 5,
    size=8,
    bold=True,
    color=MUTED
)

add_swatch(PANEL_X, 103, "#644B2D", 12, 0.5)

add_label(
    f"Minor contour ({MINOR_INTERVAL:g} m)",
    PANEL_X + 16, 102,
    PANEL_WIDTH - 16, 6,
    size=8
)

add_swatch(PANEL_X, 112, "#4B2D19", 12, 0.8)

add_label(
    f"Index contour ({INDEX_INTERVAL:g} m)",
    PANEL_X + 16, 111,
    PANEL_WIDTH - 16, 6,
    size=8
)

add_line(PANEL_X, 120, PANEL_WIDTH)


# ============================================================
# DSM STATISTICS
# ============================================================

mean_elevation = float(np.mean(valid))

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
    unit_to_meters = 1.0
    print("WARNING: DSM CRS is missing; resolution units are unknown.")

resolution_x = pixel_width * unit_to_meters
resolution_y = pixel_height * unit_to_meters

pixel_area = abs(
    geotransform[1] * geotransform[5]
    - geotransform[2] * geotransform[4]
) * unit_to_meters ** 2

survey_area_m2 = valid.size * pixel_area
survey_area_ha = survey_area_m2 / 10000

print("\n--- DSM STATISTICS ---")
print(f"Minimum elevation: {minimum:.2f} m")
print(f"Maximum elevation: {maximum:.2f} m")
print(f"Mean elevation: {mean_elevation:.2f} m")
print(f"Raster resolution: {resolution_x:.3f} x {resolution_y:.3f}")
print(f"Survey area: {survey_area_m2:.2f} m²")
print(f"Survey area: {survey_area_ha:.4f} ha")


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

add_label("Coordinate system:", PANEL_X, 133, 30, 5, size=7, bold=True)
add_label(dsm.crs().authid(), PANEL_X + 30, 133, PANEL_WIDTH - 30, 5, size=7)

add_label("Elevation range:", PANEL_X, 139, 30, 5, size=7, bold=True)
add_label(f"{minimum:.2f} – {maximum:.2f} m", PANEL_X + 30, 139, PANEL_WIDTH - 30, 5, size=7)

add_label("Mean elevation:", PANEL_X, 145, 30, 5, size=7, bold=True)
add_label(f"{mean_elevation:.2f} m", PANEL_X + 30, 145, PANEL_WIDTH - 30, 5, size=7)

add_label("Resolution:", PANEL_X, 151, 30, 5, size=7, bold=True)
add_label(f"{resolution_x:.2f} x {resolution_y:.2f}", PANEL_X + 30, 151, PANEL_WIDTH - 30, 5, size=7)

add_label("Survey area:", PANEL_X, 157, 30, 5, size=7, bold=True)
add_label(f"{survey_area_ha:.2f} ha", PANEL_X + 30, 157, PANEL_WIDTH - 30, 5, size=7)

add_label("Contour interval:", PANEL_X, 163, 30, 5, size=7, bold=True)
add_label(f"{MINOR_INTERVAL:g} m", PANEL_X + 30, 163, PANEL_WIDTH - 30, 5, size=7)


# ============================================================
# NORTH ARROW (ON MAP)
# ============================================================

NORTH_ARROW_PATH = BASE_DIR / "north_arrow.jpg"

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


# ============================================================
# SCALE BAR (ON MAP)
# ============================================================

# Target approximately 4 segments across the scale bar
target_segments = 4

map_extent = map_item.extent()
map_width_m = map_extent.width()

# Map frame width in millimeters
map_width_mm = MAP_WIDTH

# Aim for a scale bar occupying roughly 25% of the map width
target_bar_distance = map_width_m * 0.25

# Choose a readable segment distance (1, 2, 5, 10, 20, 50, ...)
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

qgs.exitQgis()

print("\nQGIS shutdown complete")
