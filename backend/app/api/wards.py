import asyncio
from datetime import datetime, timedelta, timezone
import pytz
from typing import Dict, Any, List
from fastapi import APIRouter, Query, HTTPException
import logging

from app.schemas.wards import WardsWeatherResponse, WardResponse, WardWeatherData, WardHeatStressData, WardRiskData, DailyWeather, DailyHeatStress, MLPrediction, DailyRisk
from app.gis.ward_mapping import WardMappingService
from app.data.weather_ingestion import WeatherIngestionService
from app.services.forecast import forecast_service
from app.ml.risk_classifier import classify_risk
from app.thermal.heat_index import calculate_heat_index
from app.thermal.utci import calculate_utci
from app.thermal.wbgt import calculate_wbgt
from app.thermal.radiant_temperature import calculate_mrt
from app.api.risk import risk_model

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/wards", tags=["wards"])

ward_mapping = WardMappingService()
weather_service = WeatherIngestionService()
IST = pytz.timezone('Asia/Kolkata')

async def process_ward(ward: dict) -> WardResponse:
    lat = ward["latitude"]
    lon = ward["longitude"]
    ward_no = ward["ward_no"]
    ward_name = ward["ward_name"]

    try:
        wf = await weather_service.get_weather_forecast(lat, lon, days=3)
        
        daily_weather = {}
        for hw in wf.forecast:
            dt = hw.timestamp.replace(tzinfo=timezone.utc).astimezone(IST)
            d_str = dt.strftime('%Y-%m-%d')
            if d_str not in daily_weather:
                daily_weather[d_str] = []
            daily_weather[d_str].append(hw)
            
        dates = sorted(list(daily_weather.keys()))
        if len(dates) < 3:
            raise ValueError("Not enough forecast days returned from weather provider.")
            
        today_date = dates[0]
        tomorrow_date = dates[1]
        day2_date = dates[2]

        def aggregate_weather(date_str: str) -> DailyWeather:
            hours = daily_weather.get(date_str, [])
            if not hours:
                return DailyWeather(
                    temperature_max_c=0, temperature_min_c=0, temperature_mean_c=0,
                    apparent_temperature_mean_c=0, humidity_mean_percent=0,
                    wind_speed_mean_kmh=0, precipitation_sum_mm=0, weather_condition="Unknown"
                )
            
            temps = [h.temperature for h in hours]
            rh = [h.relative_humidity for h in hours]
            wind = [h.wind_speed for h in hours]
            
            return DailyWeather(
                temperature_max_c=round(max(temps), 1),
                temperature_min_c=round(min(temps), 1),
                temperature_mean_c=round(sum(temps) / len(temps), 1),
                apparent_temperature_mean_c=round(sum(temps) / len(temps), 1),
                humidity_mean_percent=round(sum(rh) / len(rh), 1),
                wind_speed_mean_kmh=round((sum(wind) / len(wind)) * 3.6, 1),
                precipitation_sum_mm=0.0,
                weather_condition="Clear"
            )

        def get_max_hour(date_str: str):
            hours = daily_weather.get(date_str, [])
            if not hours:
                return None
            return max(hours, key=lambda h: h.temperature)

        def _get_computed_risk(date_str: str):
            h = get_max_hour(date_str)
            if not h:
                return None

            hi = calculate_heat_index(h.temperature, h.relative_humidity)
            
            mrt_res = calculate_mrt(
                temperature_c=h.temperature, 
                shortwave_rad=h.shortwave_radiation,
                direct_rad=h.direct_radiation,
                diffuse_rad=h.diffuse_radiation,
                dni=h.direct_normal_irradiance,
                latitude=lat,
                longitude=lon,
                timestamp=h.timestamp
            )
            mrt_val = mrt_res.get("value_c") if mrt_res.get("value_c") is not None else h.temperature
            
            utci = calculate_utci(h.temperature, h.relative_humidity, h.wind_speed, mrt_val)
            wbgt = calculate_wbgt(
                temperature_c=h.temperature, 
                relative_humidity=h.relative_humidity, 
                wind_speed=h.wind_speed,
                shortwave_rad=h.shortwave_radiation or 0.0,
                latitude=lat,
                longitude=lon,
                timestamp=h.timestamp,
                pressure_hpa=h.pressure or 1013.25
            )

            hi_val = hi.get("value_c")
            utci_val = utci.get("value_c")
            wbgt_val = wbgt.get("value_c")

            wbgt_dict = {"value_c": wbgt_val, "status": wbgt.get("status", "CALCULATED"), "method": "Physics"}
            utci_dict = {"value_c": utci_val, "status": utci.get("status", "CALCULATED"), "method": "Physics"}
            hi_dict = {"value_c": hi_val, "status": hi.get("status", "CALCULATED"), "method": "Physics"}

            computed = risk_model.compute_risk(
                lat=lat, lon=lon, timestamp="forecast",
                wbgt_data=wbgt_dict, utci_data=utci_dict, hi_data=hi_dict, max_temp_c=h.temperature
            )
            
            return {
                "wbgt": wbgt_val, "utci": utci_val, "hi": hi_val,
                "computed": computed
            }

        def map_ml(date_str: str) -> DailyHeatStress:
            res = _get_computed_risk(date_str)
            if res:
                computed = res["computed"]
                return DailyHeatStress(
                    wbgt=MLPrediction(prediction_c=res["wbgt"], model="Physics (STEP 3)", rmse_test_error=0.0, risk=computed.thermal_stress.indices["wbgt"].category),
                    utci=MLPrediction(prediction_c=res["utci"], model="Physics (STEP 3)", rmse_test_error=0.0, risk=computed.thermal_stress.indices["utci"].category),
                    heat_index=MLPrediction(prediction_c=res["hi"], model="Physics (STEP 3)", rmse_test_error=0.0, risk=computed.thermal_stress.indices["hi"].category)
                )
            return None

        def extract_risk(date_str: str) -> DailyRisk:
            res = _get_computed_risk(date_str)
            if res:
                computed = res["computed"]
                return DailyRisk(
                    overall=computed.thermal_stress.overall_thermal_stress,
                    wbgt=computed.thermal_stress.indices["wbgt"].category,
                    utci=computed.thermal_stress.indices["utci"].category,
                    heat_index=computed.thermal_stress.indices["hi"].category
                )
            return None

        return WardResponse(
            ward_no=ward_no,
            ward_name=ward_name,
            latitude=lat,
            longitude=lon,
            status="ok",
            weather=WardWeatherData(
                today=aggregate_weather(today_date),
                tomorrow=aggregate_weather(tomorrow_date),
                day_plus_2=aggregate_weather(day2_date)
            ),
            heat_stress=WardHeatStressData(
                today=map_ml(today_date),
                tomorrow=map_ml(tomorrow_date),
                day_plus_2=map_ml(day2_date)
            ),
            risk=WardRiskData(
                today=extract_risk(today_date),
                tomorrow=extract_risk(tomorrow_date),
                day_plus_2=extract_risk(day2_date)
            ),
            provenance={
                "source": "Open-Meteo",
                "location_method": "ward centroid"
            }
        )
    except Exception as e:
        logger.error(f"Failed to process ward {ward_no}: {e}")
        return WardResponse(
            ward_no=ward_no,
            ward_name=ward_name,
            latitude=lat,
            longitude=lon,
            status="weather_unavailable",
            error=str(e)
        )

@router.get("/weather", response_model=WardsWeatherResponse)
async def get_wards_weather():
    wards = ward_mapping.get_all_wards_with_centroids()
    
    sem = asyncio.Semaphore(15)
    async def bounded_process(ward):
        async with sem:
            return await process_ward(ward)
            
    tasks = [bounded_process(w) for w in wards]
    results = await asyncio.gather(*tasks)
    
    successful = sum(1 for r in results if r.status == "ok")
    failed = len(results) - successful

    return WardsWeatherResponse(
        location="Kochi",
        timezone="Asia/Kolkata",
        generated_at=datetime.now(IST),
        ward_count=len(wards),
        successful_wards=successful,
        failed_wards=failed,
        wards=results
    )
