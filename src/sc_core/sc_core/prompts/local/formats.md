Formatos obligatorios en toda salida estructurada:
- Fechas: ISO 8601 (AAAA-MM-DD). Si la fecha del proveedor es ambigua (por ejemplo "20/10"), interpreta día/mes y baja la confianza.
- Moneda: código ISO de tres letras (PEN, USD). Nunca conviertas entre monedas.
- Cantidades: número decimal con punto; conserva la unidad de medida del proveedor.
- Identificadores de orden: tal como aparecen en el asunto, por ejemplo {{po_name}}.
- Confianza: número entre 0 y 1; usa menos de 0.7 cuando hayas tenido que interpretar.
