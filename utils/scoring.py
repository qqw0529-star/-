# utils/scoring.py
import json
from datetime import datetime, timedelta

with open('scoring_rules.json', 'r', encoding='utf-8') as f:
    RULES = json.load(f)

# --- Date Helper Functions ---
def minguo_to_gregorian(minguo_str):
    if not minguo_str or not isinstance(minguo_str, str): return None
    try:
        parts = minguo_str.split('-')
        if len(parts) != 3: raise ValueError("日期格式應為 YYY-MM-DD")
        y = int(parts[0]) + 1911
        m = int(parts[1])
        d = int(parts[2])
        # Basic validation
        if m < 1 or m > 12 or d < 1 or d > 31: raise ValueError("日期範圍錯誤")
        # More robust validation
        dt = datetime(y, m, d)
        return dt
    except (ValueError, TypeError, AttributeError) as e:
        print(f"日期轉換錯誤: {minguo_str}, 錯誤: {e}")
        return None

def add_years(date, years):
    try:
        return date.replace(year=date.year + years)
    except ValueError: # handle leap year edge case e.g. Feb 29 + 1 year
        return date.replace(year=date.year + years, day=date.day -1 )

def subtract_days(date, days):
     return date - timedelta(days=days)
     
# --- Main Calculation Functions ---
def compute_expiry_and_notice(start_minguo):
    start = minguo_to_gregorian(start_minguo)
    if start is None: return None
    expiry = add_years(start, RULES['validity_years'])
    notice_date = expiry - timedelta(days=RULES['renewal_notice_months'] * 30) # Approx 6 months
    return {
        'start': start.strftime('%Y-%m-%d'),
        'expiry': expiry.strftime('%Y-%m-%d'), # API returns the day *after* expiry
        'notice_date': notice_date.strftime('%Y-%m-%d')
    }

def evaluate_points(record):
    res = {}
    totals = RULES['totals']
    mandatory_rules = RULES['mandatory_categories']
    im_rules = RULES['indigenous_and_multicultural']
    rule_change_date = minguo_to_gregorian(im_rules['rule_change_date_minguo'])

    # --- Basic Point Calculations & Caps ---
    prof = record.get('professional_course', 0)
    
    # QER (Quality, Ethics, Regulation)
    qer_raw = record.get('quality_ethics_regulation', 0) # Sum from inputs
    qer_counted = min(qer_raw, totals['max_quality_ethics_regulation_counted'])
    pass_qer_min = qer_counted >= totals['min_quality_ethics_regulation']
    # Check if each individual QER category is > 0 (passed from frontend)
    pass_qer_each_non_zero = (record.get('quality_points_raw', 0) > 0 and
                              record.get('ethics_points_raw', 0) > 0 and
                              record.get('regulation_points_raw', 0) > 0)
                              
    # Mandatory Courses
    mand_inputs = record.get('mandatory', {})
    mand_total_counted = 0 # Not directly used in total, but calculated for breakdown
    pass_mandatory_each = True
    mandatory_results = {}
    mandatory_total_raw = 0
    for key, rule in mandatory_rules.items():
        raw_val = mand_inputs.get(key, 0)
        passes = raw_val >= rule['min']
        if not passes: pass_mandatory_each = False
        mandatory_results[key] = {
            'value': raw_val,
            'passes': passes,
            'min_required': rule['min']
        }
        mandatory_total_raw += raw_val # Calculate raw total for the >= 10 check

    pass_mandatory_total = mandatory_total_raw >= 10 # Check total >= 10


    # --- Cultural Points (Indigenous/Multicultural) ---
    pre_change_raw = record.get('pre_change_cultural_points', 0)
    yearly_points_data = record.get('yearly_cultural_points', []) # Expect list of {'year': y, 'indigenous': i, 'multicultural': m}
    renewal_date_str = record.get('renewal_date')
    renewal_dt = minguo_to_gregorian(renewal_date_str)

    im_notes = ''
    pass_cultural_overall = False
    
    # Calculate counts
    pre_change_counted = min(pre_change_raw, im_rules['before']['max_counted'])
    
    yearly_ind_total = sum(year_data.get('indigenous', 0) for year_data in yearly_points_data)
    yearly_mul_total = sum(year_data.get('multicultural', 0) for year_data in yearly_points_data)
    yearly_total_counted = yearly_ind_total + yearly_mul_total # No cap for post-change points
    
    total_cultural_counted = pre_change_counted + yearly_total_counted

    # Determine Pass/Fail based on renewal date
    if renewal_dt:
        if renewal_dt <= rule_change_date:
            # Apply OLD rule (<= 112-06-02)
            rule = im_rules['before']
            pass_cultural_overall = (pre_change_raw >= rule['min_required'])
            im_notes = f"認證更新日 {renewal_date_str}: 適用舊制 (<= {im_rules['rule_change_date_minguo']})。"
        else:
            # Apply NEW rule (> 112-06-02)
            rule = im_rules['after']
            # Check if *each* year meets the 1+1 requirement
            pass_yearly_check = True
            for year_data in yearly_points_data:
                 # We only check years AFTER the rule change date effectively starts
                 # This assumes yearly_points_data only contains post-change relevant data
                 # A better check might involve comparing year_data['start_date'] vs rule_change_date
                 # For simplicity, based on frontend logic: assume yearly_points_data is correctly filtered/disabled
                if year_data.get('indigenous', 0) < 1 or year_data.get('multicultural', 0) < 1:
                    pass_yearly_check = False
                    break # Found a year that fails

            pass_cultural_overall = pass_yearly_check
            im_notes = f"認證更新日 {renewal_date_str}: 適用新制 (> {im_rules['rule_change_date_minguo']})。需每年各 1 分。"
            
    else:
        pass_cultural_overall = False # Cannot determine rules
        im_notes = "未提供有效認證更新日，無法判斷文化積分新舊制規則。"


    # --- Online Points & Physical Points ---
    online_raw = record.get('online_points', 0)
    online_capped = min(online_raw, totals['max_online_points'])
    pass_online_max = online_raw <= totals['max_online_points']


    # --- Final Total Calculation ---
    # Sum of counted points from each category
    total_counted = prof + qer_counted + mandatory_total_raw + total_cultural_counted
    # Note: Mandatory total raw is used because individual mandatory items don't have a cap other than the implicit 10 minimum total.
    # Note: Cultural total counted already incorporates the pre-change cap.
    
    # Check Total Points requirement
    pass_total_min = total_counted >= totals['min_total_points']
    
    # Check Physical Points requirement (Derived)
    # Physical = Total Counted - Online Raw (Because online is part of total)
    physical_points = total_counted - online_raw
    pass_physical_min = physical_points >= (totals['min_total_points'] - totals['max_online_points']) # Should be >= 80

    # --- Structure Results ---
    res['breakdown'] = {
        'professional_course': {'raw': prof},
        'quality_ethics_regulation': {'raw': qer_raw, 'counted': qer_counted},
        'quality_raw': record.get('quality_points_raw', 0), # Pass through for display
        'ethics_raw': record.get('ethics_points_raw', 0),
        'regulation_raw': record.get('regulation_points_raw', 0),
        'mandatory': mandatory_results, # Contains raw, passes, min for each
        'mandatory_total_raw': mandatory_total_raw,
        'indigenous_multicultural': {
             'pre_change_raw': pre_change_raw,
             'pre_change_counted': pre_change_counted,
             'yearly_total_raw': yearly_total_counted,
             'counted': total_cultural_counted # Final counted value
             },
        'online_points_raw': online_raw,
        'online_points_capped': online_capped, # Capped online points
        'physical_points_derived': physical_points # Derived physical points
    }

    res['total_counted'] = total_counted # Final score

    res['passes'] = {
        'total': pass_total_min,
        'professional_min': prof >= totals['min_professional_course'],
        'qer_min': pass_qer_min,
        'qer_each_non_zero': pass_qer_each_non_zero,
        'mandatory_fire': mandatory_results['fire_safety']['passes'],
        'mandatory_er': mandatory_results['emergency_response']['passes'],
        'mandatory_inf': mandatory_results['infection_control']['passes'],
        'mandatory_gen': mandatory_results['gender_sensitivity']['passes'],
        'mandatory_total': pass_mandatory_total, # Check if sum >= 10
        'mandatory_each_min': pass_mandatory_each, # Check if each >= 1
        'indigenous_multicultural': pass_cultural_overall, # The final verdict based on rules
        'online_max': pass_online_max, # Check if raw online <= 40
        'physical_min': pass_physical_min # Check if derived physical >= 80
    }
    res['notes'] = im_notes

    return res
