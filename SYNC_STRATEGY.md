# Utils Sync Strategy: DXF-diff-manager ⟷ DXF-visual-diff

## Problem Statement

`extract_labels.py` has an architectural conflict:
- **DXF-diff-manager**: Imports from external `config.py`
- **DXF-visual-diff**: Self-contained with internal `ExtractionConfig`

Both files are 99% identical (only config handling differs).

## Recommended Solution: Adaptive Config Pattern

### Strategy: Make extract_labels.py Environment-Aware

Create a **single unified version** that adapts to its environment:

```python
# Adaptive config loading - works in both projects
try:
    # Try to import from external config.py (DXF-diff-manager)
    from config import extraction_config
except ImportError:
    # Fall back to internal config (DXF-visual-diff)
    class ExtractionConfig:
        DRAWING_NUMBER_PATTERN = r'[A-Z]{2}\d{4}-\d{3}-\d{2}[A-Z]'
        SOURCE_LABEL_PROXIMITY = 80
        DWG_NO_LABEL_PROXIMITY = 80
        TITLE_PROXIMITY_X = 80
        RIGHTMOST_DRAWING_TOLERANCE = 100.0

    extraction_config = ExtractionConfig()
```

### Advantages

✅ **Single source of truth**: One file works in both projects
✅ **No sync conflicts**: Same file can be copied bidirectionally
✅ **Backward compatible**: Works with existing code immediately
✅ **Self-documenting**: Clear intent through try/except pattern
✅ **Maintainable**: Updates in one place propagate everywhere

### Master Designation

**Primary Master: DXF-diff-manager**
Rationale:
- More complex features (drawing number extraction, RevUp detection)
- More active development of extract_labels functionality
- Uses extract_labels more extensively in workflow

**Sync Direction:**
```
DXF-diff-manager → unified extract_labels.py → DXF-visual-diff
```

### Files by Sync Strategy

| File | Strategy | Master | Notes |
|------|----------|--------|-------|
| `extract_labels.py` | **Adaptive** | diff-manager | Use try/except pattern |
| `compare_dxf.py` | Direct sync | diff-manager | No config dependency |
| `label_diff.py` | Direct sync | diff-manager | No config dependency |
| `common_utils.py` | Direct sync | diff-manager | No config dependency |

### Implementation Steps

1. **Update extract_labels.py in DXF-diff-manager**
   - Replace hardcoded import with try/except pattern
   - Test that it still works with config.py

2. **Copy to DXF-visual-diff**
   - Same file works without config.py
   - Falls back to internal ExtractionConfig

3. **Verify both projects**
   - Run syntax checks
   - Test actual functionality

4. **Future updates**
   - Always update DXF-diff-manager first
   - Sync to DXF-visual-diff automatically
   - No manual merging needed

## Alternative Strategies (Not Recommended)

### ❌ Option 1: Maintain Separate Versions
- **Problem**: Code duplication, merge conflicts
- **Why rejected**: Maintenance nightmare

### ❌ Option 2: Force DXF-visual-diff to use config.py
- **Problem**: Breaks self-contained architecture
- **Why rejected**: Adds unnecessary dependency

### ❌ Option 3: Force DXF-diff-manager to internalize config
- **Problem**: Loses centralized configuration benefits
- **Why rejected**: Reduces maintainability for complex project

## Sync Script Behavior

### Updated Logic

1. **Check for divergence**
   - If files differ beyond config section: ⚠️ WARN
   - Suggest running adaptive pattern update

2. **Sync with adaptive files**
   - Copy works bidirectionally
   - Same file works in both environments

3. **Validation**
   - Test import in both environments
   - Verify extraction_config is accessible

## Testing Checklist

- [ ] DXF-diff-manager: Imports from config.py successfully
- [ ] DXF-visual-diff: Falls back to internal config successfully
- [ ] Both: extraction_config.DRAWING_NUMBER_PATTERN accessible
- [ ] Both: extract_labels() function works correctly
- [ ] Both: app.py runs without errors
- [ ] Syntax validation passes for all utils files

## Maintenance Guidelines

### When updating extract_labels.py:

1. Make changes in **DXF-diff-manager version**
2. Keep config import as try/except pattern
3. Run sync script
4. Test both projects

### When config values change:

1. Update **config.py** in DXF-diff-manager
2. Update **internal ExtractionConfig** in adaptive pattern
3. Sync to both projects

### Quarterly review:

- Check if both versions still match (except config section)
- Verify config values are synchronized
- Update SYNC_STRATEGY.md if architecture changes
