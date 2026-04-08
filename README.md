#OpenAPI Data Model Visualizer

**An interactive HTML visualization tool for OpenAPI 3.x/2.x specifications**

Transform your OpenAPI JSON or YAML schemas into beautiful, interactive visualizations with four comprehensive views: Entity Graph, API Surface, All-Schemas and All-Entities.

## Features

### **Entity Graph**
- Interactive network diagram with pan and zoom capabilities
- Color-coded entities by type and complexity
- Visual relationship mapping with cardinality indicators (1, 0..1, 1..*, 0..*)
- API root schemas highlighted with dashed borders
- Click any entity to see detailed properties and API endpoint usage

### **API Surface**
- Complete endpoint catalogue organised by tags
- HTTP method colour-coding (GET, POST, PUT, PATCH, DELETE)
- Request/response schema mapping
- Expandable cards showing detailed request bodies and response types
- Direct navigation to schema details
open
### **All Schemas**
- Grid view of all object and enum schemas
- Quick overview with field counts (required ✦ / optional ○)
- Compact display with type information
- Colour-coded by category

### **All Entities**
- Comprehensive entity browser with real-time filtering
- Search entities by name (contains string search)
- Detailed property cards showing:
  - Property names with required/optional badges
  - Data types and formats
  - Descriptions and documentation
  - Enum constraints and possible values
  - Example values
- Perfect for API documentation and developer reference
- Perfect for understanding the model of the API

## Usage

```bash
python openapi_visualizer.py <openapi.json|openapi.yaml> [-o output.html]
```

### Examples

```bash
# Generate visualization from JSON (default output name)
python openapi_visualizer.py api-spec.json

# Generate visualization from YAML
python openapi_visualizer.py api-spec.yaml

# Specify custom output file
python openapi_visualizer.py api-spec.json -o my-api-docs.html

# Works with YAML files too
python openapi_visualizer.py api-spec.yml -o my-api-docs.html
```

## Requirements

- Python 3.7+
- **For JSON files**: No external dependencies required!
- **For YAML files**: PyYAML (`pip install pyyaml`)

## Output

Generates a single, self-contained HTML file that works in any modern browser. No dependencies, no server required - just open and explore!

## Use Cases

- **API Documentation**: Generate interactive documentation from OpenAPI specs
- **Schema Analysis**: Understand complex data models and relationships
- **Developer Onboarding**: Help new team members explore API structures
- **API Design Review**: Visualize schema relationships before implementation
- **Integration Planning**: Identify entities and their properties for integration work

## Screenshots

The visualizer provides four interactive tabs:

1. **Entity Graph** - Visual network of schema relationships with zoom/pan
2. **API Surface** - Complete list of endpoints with request/response schemas
3. **All Schemas** - Grid overview of all schemas with field summaries
4. **All Entities** - Detailed property browser with filtering

## License

MIT License

Copyright (c) 2025

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Tech Stack

Pure Python 3 with minimal dependencies (PyYAML optional, only for YAML input). Output uses vanilla JavaScript with embedded SVG graphics and hash-based Content Security Policy for XSS protection.

## Contributing

Contributions are welcome! Feel free to submit issues or pull requests.

