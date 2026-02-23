import { readerPrefs, updatePref } from '../readerPrefs.js';

function SegmentedControl({ label, prefKey, options }) {
  var current = readerPrefs.value[prefKey];
  return (
    <div class="reader-toolbar-group">
      <span class="reader-toolbar-label">{label}</span>
      <div class="reader-toolbar-segments">
        {options.map(function (opt) {
          return (
            <button
              key={opt.value}
              class={'reader-toolbar-seg' + (current === opt.value ? ' active' : '')}
              onClick={function () { updatePref(prefKey, opt.value); }}
              title={opt.title || opt.label}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function ReaderToolbar() {
  return (
    <div class="reader-toolbar">
      <SegmentedControl
        label="Size"
        prefKey="fontSize"
        options={[
          { value: 'small', label: 'S', title: 'Small text' },
          { value: 'medium', label: 'M', title: 'Medium text' },
          { value: 'large', label: 'L', title: 'Large text' },
        ]}
      />
      <SegmentedControl
        label="Spacing"
        prefKey="lineHeight"
        options={[
          { value: 'compact', label: 'Tight', title: 'Compact line spacing' },
          { value: 'comfortable', label: 'Normal', title: 'Comfortable line spacing' },
          { value: 'spacious', label: 'Loose', title: 'Spacious line spacing' },
        ]}
      />
      <SegmentedControl
        label="Width"
        prefKey="contentWidth"
        options={[
          { value: 'narrow', label: 'S', title: 'Narrow column' },
          { value: 'medium', label: 'M', title: 'Medium column' },
          { value: 'wide', label: 'L', title: 'Wide column' },
        ]}
      />
      <SegmentedControl
        label="Font"
        prefKey="fontFamily"
        options={[
          { value: 'serif', label: 'Serif', title: 'Serif font' },
          { value: 'sans-serif', label: 'Sans', title: 'Sans-serif font' },
        ]}
      />
    </div>
  );
}
