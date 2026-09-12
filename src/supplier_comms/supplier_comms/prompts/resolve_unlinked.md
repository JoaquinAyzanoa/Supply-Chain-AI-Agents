Tarea: decidir a qué orden de compra pertenece un correo de proveedor que las reglas automáticas no pudieron asociar.

Tienes el texto del correo y la lista de órdenes candidatas (con proveedor, productos, cantidades y fechas). Elige una orden solo si el correo la identifica con claridad: menciona su número, sus productos o cantidades, o responde a algo que solo esa orden explica. Si ninguna candidata encaja, o si dos encajan igual de bien, responde po_name vacío.

Indica la confianza entre 0 y 1 (menos de 0.7 cuando hayas tenido que suponer) y la razón en una frase que un comprador pueda verificar.

Responde únicamente con JSON con las claves po_name, confidence y reason.
