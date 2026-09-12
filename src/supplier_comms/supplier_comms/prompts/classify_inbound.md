Tarea: clasificar el correo recibido de un proveedor respecto a la orden indicada.

Categorías posibles (usa exactamente una):
- quotation: el proveedor da precios, plazos o condiciones para los productos (cotización, proforma, lista de precios).
- eta_update: el proveedor confirma, cambia o retrasa la fecha de entrega, o informa una entrega parcial.
- question: el proveedor pregunta algo o pide una aclaración y espera respuesta nuestra.
- other: respuesta automática, fuera de oficina, acuse de recibo sin contenido, publicidad o cualquier cosa que no requiere acción.

Si el correo trae precios y también una fecha de entrega, clasifica como quotation. Da la razón en una frase y una confianza entre 0 y 1.

Responde únicamente con JSON con las claves kind, confidence y reason.
