export function MapAnnotation() {
  return (
    <svg className="map-annotation" id="map-annotation" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 310 225" width="310" height="225">
      <path className="ann-stroke" d="M 126 176 C 128 156 100 141 65 143 C 29 145 2 161 4 181 C 6 201 35 215 69 213 C 103 211 127 197 126 176" />
      <g transform="rotate(-10, 122, 157)">
        <path className="ann-stroke" d="M 122 157 C 157 123 191 97 216 76" />
        <path className="ann-stroke" d="M 216 76 L 205 89" />
        <path className="ann-stroke" d="M 216 76 L 203 71" />
        <text className="ann-text" x="222" y="70">Knowledge</text>
      </g>
    </svg>
  );
}
