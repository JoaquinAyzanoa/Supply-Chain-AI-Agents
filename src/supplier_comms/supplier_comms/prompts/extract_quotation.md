Tarea: extraer del correo del proveedor (y de los adjuntos, si hay texto) los datos estructurados que afectan a la orden indicada.

Reglas:
- Empareja cada producto cotizado con la línea de la orden cuyo producto coincida y pon su id en po_line_id. Si no hay coincidencia clara, deja po_line_id vacío y baja la confianza de esa línea.
- unit_price es el precio por unidad, sin impuestos si el proveedor los separa. currency es el código ISO de tres letras que el proveedor usa; si no lo indica, deja currency vacío (no asumas la moneda de la orden).
- lead_days es el plazo de entrega en días si el proveedor lo da en días; min_qty la cantidad mínima si la menciona.
- eta_date_raw es la fecha de entrega tal como la escribió el proveedor; eta_date es esa fecha en formato AAAA-MM-DD según la fecha de hoy indicada. Si el proveedor no da fecha, deja ambas vacías. Si la fecha es ambigua, interpreta día/mes y baja confidence.
- notes: condiciones relevantes en una o dos frases (validez, pago, entregas parciales). Nada más.
- confidence global entre 0 y 1: menos de 0.7 cuando hayas tenido que interpretar.

Responde únicamente con JSON que cumpla el esquema indicado.
