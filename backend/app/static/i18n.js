// Übersetzungen für die UI. OUTPUT_LANGUAGE aus der .env bestimmt (über /api/config),
// welche dieser Sprachen verwendet wird. Nicht abgedeckte Sprachen fallen auf Englisch zurück.
const TRANSLATIONS = {
  de: {
    pageTitle: 'Kochbuch → Tandoor',
    brandCookbook: 'Kochbuch',
    tandoorChecking: 'Tandoor wird geprüft …',
    tandoorConnected: 'Tandoor verbunden',
    tandoorUnreachable: 'Tandoor nicht erreichbar',
    tandoorUnknown: 'Tandoor-Status unbekannt',
    tandoorConnFailedPrefix: 'Tandoor-Verbindung fehlgeschlagen:',
    tandoorUnknownError: 'Unbekannter Fehler.',
    tandoorNoConfigHint: 'Konnte /api/tandoor/status nicht abfragen – ist der Container gestartet?',

    uploadHeadline: 'Aus dem Kochbuch in deine Rezeptdatenbank',
    uploadLede: 'Lade ein PDF hoch. Claude liest jede Seite, erkennt einzelne Rezepte samt Zutaten, Zeiten und Bildern – du wählst danach aus, was in Tandoor landet.',
    dropzoneTitle: 'PDF hierher ziehen',
    dropzoneSubtitle: 'oder klicken zum Auswählen',
    onlyPdfError: 'Bitte eine PDF-Datei auswählen.',
    uploadingFile: '„{filename}" wird hochgeladen …',
    uploadFailedPrefix: 'Upload fehlgeschlagen',

    processingDefault: 'Claude liest das Kochbuch und erkennt Rezepte – das kann je nach Umfang einige Minuten dauern …',
    processingReadingPdf: 'PDF wird gelesen …',
    processingErrorPrefix: 'Fehler:',
    jobNotFoundError: 'Job nicht gefunden.',

    cookbookLabel: 'Kochbuch in Tandoor',
    cookbookPlaceholder: 'Name des Kochbuchs …',
    cookbookHint: 'Wird angelegt, falls es noch nicht existiert.',

    recipesFound: '{n} Rezept gefunden',
    recipesFoundPlural: '{n} Rezepte gefunden',
    toggleAll: 'alle',
    selectRecipeHint: 'Wähle links ein Rezept aus.',
    noIngredients: 'Keine Zutaten erkannt.',
    noSteps: 'Keine Zubereitungsschritte erkannt.',
    noTags: 'keine',
    noImage: 'kein Bild',

    fieldImage: 'Bild',
    fieldDetails: 'Angaben',
    fieldServings: 'Portionen',
    fieldPrep: 'Vorbereitung (Min.)',
    fieldCook: 'Kochzeit (Min.)',
    fieldTags: 'Tags',
    fieldIngredients: 'Zutaten',
    fieldSteps: 'Zubereitung',
    placeholderAmount: 'Menge',
    placeholderUnit: 'Einheit',
    placeholderIngredient: 'Zutat',

    duplicateExactWarning: 'Möglicherweise bereits in Tandoor vorhanden (exakter Titel-Treffer): „{name}". Deshalb aktuell abgewählt.',
    duplicateSimilarWarning: 'Ein ähnlich benanntes Rezept existiert bereits in Tandoor: „{name}".',
    duplicateBadgeExact: 'evtl. Duplikat',
    duplicateBadgeSimilar: 'ähnlich vorhanden',

    selectionCount: '{n} von {total} ausgewählt',
    importBtn: 'In Tandoor importieren',
    importBtnLoading: 'Importiere …',
    statusImporting: 'Wird importiert …',
    statusImported: 'Importiert',
    statusError: 'Fehler',
    importFailedAlertPrefix: 'Import fehlgeschlagen:',

    modalTitleSuccess: 'Erfolgreich importiert!',
    modalTitlePartial: 'Teilweise importiert',
    modalTitleFailed: 'Import fehlgeschlagen',
    modalSummaryLine: '{ok} von {total} Rezept(en) erfolgreich in Tandoor angelegt.',
    modalCookbookLine: 'Kochbuch: „{name}"',
    modalCookbookWarningLine: 'Hinweis zum Kochbuch: {warning}',
    modalFailedLine: '{failed} Rezept(e) mit Fehler – Details siehe Rezeptliste links.',
    modalClose: 'Schließen',
    modalNewUpload: 'Weiteres PDF hochladen',

    pageLabel: 'S.',
  },

  en: {
    pageTitle: 'Cookbook → Tandoor',
    brandCookbook: 'Cookbook',
    tandoorChecking: 'Checking Tandoor …',
    tandoorConnected: 'Tandoor connected',
    tandoorUnreachable: 'Tandoor unreachable',
    tandoorUnknown: 'Tandoor status unknown',
    tandoorConnFailedPrefix: 'Tandoor connection failed:',
    tandoorUnknownError: 'Unknown error.',
    tandoorNoConfigHint: 'Could not reach /api/tandoor/status – is the container running?',

    uploadHeadline: 'Turn your cookbook into a recipe database',
    uploadLede: 'Upload a PDF. Claude reads every page, detects individual recipes with ingredients, times and images – then you choose what goes into Tandoor.',
    dropzoneTitle: 'Drop a PDF here',
    dropzoneSubtitle: 'or click to choose a file',
    onlyPdfError: 'Please select a PDF file.',
    uploadingFile: 'Uploading "{filename}" …',
    uploadFailedPrefix: 'Upload failed',

    processingDefault: 'Claude is reading the cookbook and detecting recipes – this can take a few minutes depending on its size …',
    processingReadingPdf: 'Reading PDF …',
    processingErrorPrefix: 'Error:',
    jobNotFoundError: 'Job not found.',

    cookbookLabel: 'Cookbook in Tandoor',
    cookbookPlaceholder: 'Cookbook name …',
    cookbookHint: 'Will be created if it does not exist yet.',

    recipesFound: '{n} recipe found',
    recipesFoundPlural: '{n} recipes found',
    toggleAll: 'all',
    selectRecipeHint: 'Select a recipe on the left.',
    noIngredients: 'No ingredients detected.',
    noSteps: 'No preparation steps detected.',
    noTags: 'none',
    noImage: 'no image',

    fieldImage: 'Image',
    fieldDetails: 'Details',
    fieldServings: 'Servings',
    fieldPrep: 'Prep time (min)',
    fieldCook: 'Cook time (min)',
    fieldTags: 'Tags',
    fieldIngredients: 'Ingredients',
    fieldSteps: 'Instructions',
    placeholderAmount: 'Amount',
    placeholderUnit: 'Unit',
    placeholderIngredient: 'Ingredient',

    duplicateExactWarning: 'Might already exist in Tandoor (exact title match): "{name}". Deselected for that reason.',
    duplicateSimilarWarning: 'A similarly named recipe already exists in Tandoor: "{name}".',
    duplicateBadgeExact: 'possible duplicate',
    duplicateBadgeSimilar: 'similar exists',

    selectionCount: '{n} of {total} selected',
    importBtn: 'Import to Tandoor',
    importBtnLoading: 'Importing …',
    statusImporting: 'Importing …',
    statusImported: 'Imported',
    statusError: 'Error',
    importFailedAlertPrefix: 'Import failed:',

    modalTitleSuccess: 'Imported successfully!',
    modalTitlePartial: 'Partially imported',
    modalTitleFailed: 'Import failed',
    modalSummaryLine: '{ok} of {total} recipe(s) successfully created in Tandoor.',
    modalCookbookLine: 'Cookbook: "{name}"',
    modalCookbookWarningLine: 'Cookbook note: {warning}',
    modalFailedLine: '{failed} recipe(s) failed – see the recipe list on the left for details.',
    modalClose: 'Close',
    modalNewUpload: 'Upload another PDF',

    pageLabel: 'p.',
  },

  fr: {
    pageTitle: 'Livre de cuisine → Tandoor',
    brandCookbook: 'Livre de cuisine',
    tandoorChecking: 'Vérification de Tandoor …',
    tandoorConnected: 'Tandoor connecté',
    tandoorUnreachable: 'Tandoor inaccessible',
    tandoorUnknown: 'Statut Tandoor inconnu',
    tandoorConnFailedPrefix: 'Échec de la connexion à Tandoor :',
    tandoorUnknownError: 'Erreur inconnue.',
    tandoorNoConfigHint: 'Impossible de joindre /api/tandoor/status – le conteneur est-il démarré ?',

    uploadHeadline: 'Transformez votre livre de cuisine en base de recettes',
    uploadLede: 'Importez un PDF. Claude lit chaque page, détecte les recettes avec ingrédients, temps et images – vous choisissez ensuite ce qui part vers Tandoor.',
    dropzoneTitle: 'Déposez un PDF ici',
    dropzoneSubtitle: 'ou cliquez pour en choisir un',
    onlyPdfError: 'Veuillez sélectionner un fichier PDF.',
    uploadingFile: 'Téléversement de « {filename} » …',
    uploadFailedPrefix: 'Échec du téléversement',

    processingDefault: 'Claude lit le livre de cuisine et détecte les recettes – cela peut prendre plusieurs minutes selon la taille du fichier …',
    processingReadingPdf: 'Lecture du PDF …',
    processingErrorPrefix: 'Erreur :',
    jobNotFoundError: 'Tâche introuvable.',

    cookbookLabel: 'Livre de cuisine dans Tandoor',
    cookbookPlaceholder: 'Nom du livre de cuisine …',
    cookbookHint: "Sera créé s'il n'existe pas encore.",

    recipesFound: '{n} recette trouvée',
    recipesFoundPlural: '{n} recettes trouvées',
    toggleAll: 'tout',
    selectRecipeHint: 'Sélectionnez une recette à gauche.',
    noIngredients: 'Aucun ingrédient détecté.',
    noSteps: 'Aucune étape de préparation détectée.',
    noTags: 'aucun',
    noImage: 'aucune image',

    fieldImage: 'Image',
    fieldDetails: 'Détails',
    fieldServings: 'Portions',
    fieldPrep: 'Préparation (min)',
    fieldCook: 'Cuisson (min)',
    fieldTags: 'Tags',
    fieldIngredients: 'Ingrédients',
    fieldSteps: 'Préparation',
    placeholderAmount: 'Quantité',
    placeholderUnit: 'Unité',
    placeholderIngredient: 'Ingrédient',

    duplicateExactWarning: 'Existe peut-être déjà dans Tandoor (titre identique) : « {name} ». Désélectionné pour cette raison.',
    duplicateSimilarWarning: 'Une recette au nom similaire existe déjà dans Tandoor : « {name} ».',
    duplicateBadgeExact: 'doublon possible',
    duplicateBadgeSimilar: 'similaire existant',

    selectionCount: '{n} sur {total} sélectionnée(s)',
    importBtn: 'Importer dans Tandoor',
    importBtnLoading: 'Importation …',
    statusImporting: 'Importation …',
    statusImported: 'Importé',
    statusError: 'Erreur',
    importFailedAlertPrefix: 'Échec de l\'importation :',

    modalTitleSuccess: 'Importation réussie !',
    modalTitlePartial: 'Importation partielle',
    modalTitleFailed: "Échec de l'importation",
    modalSummaryLine: '{ok} recette(s) sur {total} créée(s) avec succès dans Tandoor.',
    modalCookbookLine: 'Livre de cuisine : « {name} »',
    modalCookbookWarningLine: 'Remarque sur le livre de cuisine : {warning}',
    modalFailedLine: '{failed} recette(s) en échec – voir la liste à gauche pour les détails.',
    modalClose: 'Fermer',
    modalNewUpload: 'Importer un autre PDF',

    pageLabel: 'p.',
  },

  it: {
    pageTitle: 'Ricettario → Tandoor',
    brandCookbook: 'Ricettario',
    tandoorChecking: 'Verifica di Tandoor …',
    tandoorConnected: 'Tandoor connesso',
    tandoorUnreachable: 'Tandoor non raggiungibile',
    tandoorUnknown: 'Stato di Tandoor sconosciuto',
    tandoorConnFailedPrefix: 'Connessione a Tandoor non riuscita:',
    tandoorUnknownError: 'Errore sconosciuto.',
    tandoorNoConfigHint: 'Impossibile contattare /api/tandoor/status – il container è avviato?',

    uploadHeadline: 'Trasforma il tuo ricettario in un database di ricette',
    uploadLede: 'Carica un PDF. Claude legge ogni pagina, individua le singole ricette con ingredienti, tempi e immagini – poi scegli cosa importare in Tandoor.',
    dropzoneTitle: 'Trascina qui un PDF',
    dropzoneSubtitle: 'oppure clicca per selezionarlo',
    onlyPdfError: 'Seleziona un file PDF.',
    uploadingFile: 'Caricamento di "{filename}" …',
    uploadFailedPrefix: 'Caricamento non riuscito',

    processingDefault: 'Claude sta leggendo il ricettario e individuando le ricette – a seconda delle dimensioni può richiedere alcuni minuti …',
    processingReadingPdf: 'Lettura del PDF …',
    processingErrorPrefix: 'Errore:',
    jobNotFoundError: 'Job non trovato.',

    cookbookLabel: 'Ricettario in Tandoor',
    cookbookPlaceholder: 'Nome del ricettario …',
    cookbookHint: 'Verrà creato se non esiste ancora.',

    recipesFound: '{n} ricetta trovata',
    recipesFoundPlural: '{n} ricette trovate',
    toggleAll: 'tutte',
    selectRecipeHint: 'Seleziona una ricetta a sinistra.',
    noIngredients: 'Nessun ingrediente rilevato.',
    noSteps: 'Nessun passaggio di preparazione rilevato.',
    noTags: 'nessuno',
    noImage: 'nessuna immagine',

    fieldImage: 'Immagine',
    fieldDetails: 'Dettagli',
    fieldServings: 'Porzioni',
    fieldPrep: 'Preparazione (min)',
    fieldCook: 'Cottura (min)',
    fieldTags: 'Tag',
    fieldIngredients: 'Ingredienti',
    fieldSteps: 'Preparazione',
    placeholderAmount: 'Quantità',
    placeholderUnit: 'Unità',
    placeholderIngredient: 'Ingrediente',

    duplicateExactWarning: 'Potrebbe già esistere in Tandoor (titolo identico): "{name}". Deselezionata per questo motivo.',
    duplicateSimilarWarning: 'Esiste già una ricetta con nome simile in Tandoor: "{name}".',
    duplicateBadgeExact: 'possibile duplicato',
    duplicateBadgeSimilar: 'simile presente',

    selectionCount: '{n} di {total} selezionate',
    importBtn: 'Importa in Tandoor',
    importBtnLoading: 'Importazione …',
    statusImporting: 'Importazione …',
    statusImported: 'Importata',
    statusError: 'Errore',
    importFailedAlertPrefix: 'Importazione non riuscita:',

    modalTitleSuccess: 'Importazione riuscita!',
    modalTitlePartial: 'Importazione parziale',
    modalTitleFailed: 'Importazione non riuscita',
    modalSummaryLine: '{ok} di {total} ricette create con successo in Tandoor.',
    modalCookbookLine: 'Ricettario: "{name}"',
    modalCookbookWarningLine: 'Nota sul ricettario: {warning}',
    modalFailedLine: '{failed} ricetta/e con errore – dettagli nella lista a sinistra.',
    modalClose: 'Chiudi',
    modalNewUpload: 'Carica un altro PDF',

    pageLabel: 'p.',
  },

  es: {
    pageTitle: 'Recetario → Tandoor',
    brandCookbook: 'Recetario',
    tandoorChecking: 'Comprobando Tandoor …',
    tandoorConnected: 'Tandoor conectado',
    tandoorUnreachable: 'Tandoor no disponible',
    tandoorUnknown: 'Estado de Tandoor desconocido',
    tandoorConnFailedPrefix: 'Fallo de conexión con Tandoor:',
    tandoorUnknownError: 'Error desconocido.',
    tandoorNoConfigHint: 'No se pudo acceder a /api/tandoor/status – ¿está iniciado el contenedor?',

    uploadHeadline: 'Convierte tu recetario en una base de datos de recetas',
    uploadLede: 'Sube un PDF. Claude lee cada página, detecta recetas individuales con ingredientes, tiempos e imágenes – luego eliges qué se importa a Tandoor.',
    dropzoneTitle: 'Suelta un PDF aquí',
    dropzoneSubtitle: 'o haz clic para elegir uno',
    onlyPdfError: 'Selecciona un archivo PDF.',
    uploadingFile: 'Subiendo "{filename}" …',
    uploadFailedPrefix: 'Error al subir el archivo',

    processingDefault: 'Claude está leyendo el recetario y detectando recetas – según el tamaño puede tardar varios minutos …',
    processingReadingPdf: 'Leyendo el PDF …',
    processingErrorPrefix: 'Error:',
    jobNotFoundError: 'Tarea no encontrada.',

    cookbookLabel: 'Recetario en Tandoor',
    cookbookPlaceholder: 'Nombre del recetario …',
    cookbookHint: 'Se creará si aún no existe.',

    recipesFound: '{n} receta encontrada',
    recipesFoundPlural: '{n} recetas encontradas',
    toggleAll: 'todas',
    selectRecipeHint: 'Selecciona una receta a la izquierda.',
    noIngredients: 'No se detectaron ingredientes.',
    noSteps: 'No se detectaron pasos de preparación.',
    noTags: 'ninguno',
    noImage: 'sin imagen',

    fieldImage: 'Imagen',
    fieldDetails: 'Detalles',
    fieldServings: 'Porciones',
    fieldPrep: 'Preparación (min)',
    fieldCook: 'Cocción (min)',
    fieldTags: 'Etiquetas',
    fieldIngredients: 'Ingredientes',
    fieldSteps: 'Preparación',
    placeholderAmount: 'Cantidad',
    placeholderUnit: 'Unidad',
    placeholderIngredient: 'Ingrediente',

    duplicateExactWarning: 'Puede que ya exista en Tandoor (título idéntico): "{name}". Deseleccionada por este motivo.',
    duplicateSimilarWarning: 'Ya existe una receta con un nombre similar en Tandoor: "{name}".',
    duplicateBadgeExact: 'posible duplicado',
    duplicateBadgeSimilar: 'similar existente',

    selectionCount: '{n} de {total} seleccionadas',
    importBtn: 'Importar a Tandoor',
    importBtnLoading: 'Importando …',
    statusImporting: 'Importando …',
    statusImported: 'Importada',
    statusError: 'Error',
    importFailedAlertPrefix: 'Error al importar:',

    modalTitleSuccess: '¡Importado con éxito!',
    modalTitlePartial: 'Importación parcial',
    modalTitleFailed: 'Error al importar',
    modalSummaryLine: '{ok} de {total} receta(s) creada(s) con éxito en Tandoor.',
    modalCookbookLine: 'Recetario: "{name}"',
    modalCookbookWarningLine: 'Nota sobre el recetario: {warning}',
    modalFailedLine: '{failed} receta(s) con error – ver detalles en la lista de la izquierda.',
    modalClose: 'Cerrar',
    modalNewUpload: 'Subir otro PDF',

    pageLabel: 'p.',
  },
};

let LANG_CODE = 'de';
let T = TRANSLATIONS.de;

function t(key) {
  return (T && T[key] !== undefined) ? T[key] : ((TRANSLATIONS.en[key] !== undefined) ? TRANSLATIONS.en[key] : key);
}

function tf(key, params) {
  let str = t(key);
  Object.keys(params || {}).forEach((k) => {
    str = str.split(`{${k}}`).join(params[k]);
  });
  return str;
}

function applyStaticTranslations() {
  document.querySelectorAll('[data-i18n]').forEach((elm) => {
    const key = elm.getAttribute('data-i18n');
    elm.textContent = t(key);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach((elm) => {
    const key = elm.getAttribute('data-i18n-placeholder');
    elm.setAttribute('placeholder', t(key));
  });
}

async function initI18n() {
  try {
    const res = await fetch('/api/config');
    const data = await res.json();
    LANG_CODE = data.language_code || 'de';
  } catch {
    LANG_CODE = 'de';
  }
  T = TRANSLATIONS[LANG_CODE] || TRANSLATIONS.en;
  document.documentElement.setAttribute('lang', LANG_CODE);
  applyStaticTranslations();
}
