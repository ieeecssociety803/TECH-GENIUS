import re

with open('backend/app/api/wards.py', 'r', encoding='utf-8') as f:
    content = f.read()

replacement = '''
        from app.thermal.heat_index import calculate_heat_index
        from app.thermal.utci import calculate_utci
        from app.thermal.wbgt import calculate_wbgt
        from app.thermal.radiant_temperature import calculate_mrt
        from app.api.risk import risk_model

        def get_max_hour(date_str: str):
            hours = daily_weather.get(date_str, [])
            if not hours:
                return None
            return max(hours, key=lambda h: h.temperature)

        def _get_computed_risk(date_str: str):
            h = get_max_hour(date_str)
            if not h:
                return None

            # 1. Physics Calculations
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

            # Map missing to none
            hi_val = hi.get("value_c")
            utci_val = utci.get("value_c")
            wbgt_val = wbgt.get("value_c")

            # 2. Risk Evaluation via STEP 5
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
'''

# Find where to replace
# We want to replace from "from app.api.risk import risk_model" down to "def extract_risk(horizon_idx: int) -> DailyRisk:" and its body
pattern = r"        # Import risk model instance.*?return None"
content = re.sub(pattern, replacement.strip(), content, flags=re.DOTALL)

# Now we need to fix the calls map_ml(0) to map_ml(today_date)
content = content.replace("map_ml(0)", "map_ml(today_date)")
content = content.replace("map_ml(1)", "map_ml(tomorrow_date)")
content = content.replace("map_ml(2)", "map_ml(day2_date)")
content = content.replace("extract_risk(0)", "extract_risk(today_date)")
content = content.replace("extract_risk(1)", "extract_risk(tomorrow_date)")
content = content.replace("extract_risk(2)", "extract_risk(day2_date)")

with open('backend/app/api/wards.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Updated wards.py")
