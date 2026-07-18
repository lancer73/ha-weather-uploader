"""Shared fixtures."""

import pytest


@pytest.fixture
def sample_data():
    """Return a full normalized observation in internal units."""
    return {
        "temperature": 20.0,
        "dewpoint": 10.0,
        "humidity": 52.0,
        "pressure_absolute": 1000.0,
        "pressure_relative": 1013.25,
        "wind_speed": 5.0,
        "wind_gust": 9.0,
        "wind_direction": 180.0,
        "wind_gust_direction": 190.0,
        "rain_rate": 5.08,
        "rain_hourly": 2.54,
        "rain_daily": 25.4,
        "rain_weekly": 50.8,
        "rain_monthly": 101.6,
        "rain_yearly": 254.0,
        "solar_radiation": 450.0,
        "uv_index": 4.0,
        "indoor_temperature": 21.0,
        "indoor_humidity": 45.0,
        "soil_temperature": 15.0,
        "soil_moisture": 30.0,
        "leaf_wetness": 12.0,
        "pm25": 8.0,
        "pm10": 14.0,
        "visibility": 16.09344,
    }
