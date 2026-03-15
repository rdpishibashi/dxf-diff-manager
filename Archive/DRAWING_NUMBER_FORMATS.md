# Drawing Number Format Support

## Issue Identified

The DXF-diff-manager project uses TWO drawing number formats:

1. **Long format**: `XX0000-000-00X` (e.g., `EE6668-405-00A`)
2. **Short format**: `XX0000-000X` (e.g., `EE6668-405A`)

The original regex pattern only matched the long format:
```regex
[A-Z]{2}\d{4}-\d{3}-\d{2}[A-Z]  # ❌ Only matches long format
```

## Solution Implemented

Updated the pattern to support BOTH formats using optional non-capturing group:

```regex
[A-Z]{2}\d{4}-\d{3}(?:-\d{2})?[A-Z]  # ✅ Matches both formats
```

### Pattern Breakdown

```
[A-Z]{2}        # Exactly 2 uppercase letters (e.g., EE, DE, XX)
\d{4}           # Exactly 4 digits (e.g., 6668, 5313)
-               # Literal dash
\d{3}           # Exactly 3 digits (e.g., 405, 008)
(?:-\d{2})?     # OPTIONAL: dash + 2 digits (e.g., -00, -02)
                # (?:...) = non-capturing group
                # ? = zero or one occurrence
[A-Z]           # Exactly 1 uppercase letter suffix (e.g., A, B, Z)
```

## Format Examples

### ✅ Long Format (12 characters + dashes)
- `EE6668-405-00A`
- `DE5313-008-02B`
- `XX1234-567-89Z`

### ✅ Short Format (9 characters + dash)
- `EE6668-405A`
- `DE5313-008B`
- `YY5678-901C`

### ❌ Invalid Formats (Correctly Rejected)
- `EE6668-405` - Missing letter suffix
- `E6668-405A` - Only 1 letter prefix (needs 2)
- `EE66-405A` - Only 2 digits in first number (needs 4)
- `EE6668405A` - Missing dashes

## Files Updated

### 1. `/DXF-diff-manager/config.py`
```python
class ExtractionConfig:
    # 両フォーマット対応: XX0000-000-00X（長）、XX0000-000X（短）
    DRAWING_NUMBER_PATTERN = r'[A-Z]{2}\d{4}-\d{3}(?:-\d{2})?[A-Z]'
```

### 2. `/DXF-diff-manager/utils/extract_labels.py`
```python
class ExtractionConfig:  # Fallback for DXF-visual-diff
    # 両フォーマット対応: XX0000-000-00X（長）、XX0000-000X（短）
    DRAWING_NUMBER_PATTERN = r'[A-Z]{2}\d{4}-\d{3}(?:-\d{2})?[A-Z]'
```

### 3. `/DXF-visual-diff/utils/extract_labels.py`
- Synced from DXF-diff-manager
- Uses fallback internal config with same pattern

## Test Results

All test cases passed:

| Test Input | Expected Output | Status |
|------------|----------------|--------|
| `DWG No.: EE6668-405-00A` | `['EE6668-405-00A']` | ✅ PASS |
| `Drawing: EE6668-405A` | `['EE6668-405A']` | ✅ PASS |
| `DE5313-008-02B and DE5313-008B` | `['DE5313-008-02B', 'DE5313-008B']` | ✅ PASS |
| `XX1234-567-89A, YY5678-901B, ZZ9012-345-67C` | All 3 numbers | ✅ PASS |

## Technical Details

### Why Non-Capturing Group `(?:...)`?

Using `(?:-\d{2})?` instead of `(-\d{2})?`:

**Problem with capturing group:**
```python
import re
pattern = r'[A-Z]{2}\d{4}-\d{3}(-\d{2})?[A-Z]'
text = "EE6668-405A"
re.findall(pattern, text)  # Returns: ['']  ❌ Wrong!
```

**Solution with non-capturing group:**
```python
import re
pattern = r'[A-Z]{2}\d{4}-\d{3}(?:-\d{2})?[A-Z]'
text = "EE6668-405A"
re.findall(pattern, text)  # Returns: ['EE6668-405A']  ✅ Correct!
```

### Why `?` for Optional?

The `?` quantifier means "zero or one" occurrence:
- `(?:-\d{2})?` matches `-00`, `-01`, ..., `-99` OR nothing
- This makes the `-\d{2}` part optional
- Pattern works for both long and short formats

## Verification

Both projects verified:

### DXF-diff-manager ✅
- Imports from `config.py`
- Pattern: `[A-Z]{2}\d{4}-\d{3}(?:-\d{2})?[A-Z]`
- Both formats extracted correctly

### DXF-visual-diff ✅
- Uses fallback internal config
- Pattern: `[A-Z]{2}\d{4}-\d{3}(?:-\d{2})?[A-Z]`
- Both formats extracted correctly

## Impact

### Functions Affected
- `extract_drawing_numbers()` - Now handles both formats
- `determine_drawing_number_types()` - Works with both formats
- RevUp detection - Works with both formats
- Parent-child pairing - Works with both formats

### User-Visible Changes
- ✅ More drawing numbers detected automatically
- ✅ Short format drawings now processed correctly
- ✅ No manual intervention needed for either format
- ✅ Backward compatible (long format still works)

## Maintenance

When updating drawing number patterns:

1. **Always update both locations:**
   - `DXF-diff-manager/config.py`
   - `DXF-diff-manager/utils/extract_labels.py` (fallback config)

2. **Use non-capturing groups for optional parts:**
   - `(?:...)` not `(...)`
   - Prevents `re.findall()` issues

3. **Test both formats:**
   ```bash
   cd DXF-diff-manager
   python3 -c "from utils.extract_labels import extract_drawing_numbers; \
               print(extract_drawing_numbers('EE6668-405-00A and EE6668-405A'))"
   ```

4. **Sync to DXF-visual-diff:**
   ```bash
   python3 sync_utils.py
   ```

## Related Documentation

- `SYNC_STRATEGY.md` - Adaptive config pattern strategy
- `SYNC_SOLUTION_SUMMARY.md` - Utils sync implementation
- `DXF-diff-manager/README.md` - User documentation
