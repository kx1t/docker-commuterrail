<?php

declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');

if ($_SERVER['REQUEST_METHOD'] !== 'GET') {
    http_response_code(405);
    echo json_encode(['error' => 'Method not allowed']);
    exit;
}

$origin = $_SERVER['HTTP_ORIGIN'] ?? '';
$host = $_SERVER['HTTP_HOST'] ?? '';
if ($origin !== '') {
    $originHost = parse_url($origin, PHP_URL_HOST);
    if (!is_string($originHost) || $originHost !== $host) {
        http_response_code(403);
        echo json_encode(['error' => 'Forbidden']);
        exit;
    }
}

$config = [];

$secretJsonFiles = [];
$envJsonFile = getenv('COMMUTERRAIL_RUNTIME_CONFIG_JSON');
if (is_string($envJsonFile) && $envJsonFile !== '') {
    $secretJsonFiles[] = $envJsonFile;
}
$secretJsonFiles[] = '/opt/webproxy/secrets/runtime-config.json';
$secretJsonFiles[] = '/opt/webproxy/secrets/commuter-runtime-config.json';

foreach ($secretJsonFiles as $secretJsonFile) {
    if (!is_file($secretJsonFile) || !is_readable($secretJsonFile)) {
        continue;
    }

    $raw = file_get_contents($secretJsonFile);
    if ($raw === false) {
        continue;
    }

    $decoded = json_decode($raw, true);
    if (is_array($decoded)) {
        $config = $decoded;
        break;
    }
}

if (!$config) {
    $secretPhpFile = getenv('COMMUTERRAIL_RUNTIME_CONFIG_PHP') ?: '/opt/webproxy/secrets/commuterrail-config.php';
    if (is_string($secretPhpFile) && is_file($secretPhpFile) && is_readable($secretPhpFile)) {
        $loaded = require $secretPhpFile;
        if (is_array($loaded)) {
            $config = $loaded;
        }
    }
}

$bostonKey = (string) ($config['boston_api_key'] ?? getenv('COMMUTERRAIL_BOSTON_API_KEY') ?: '');
$parisKey = (string) ($config['paris_api_key'] ?? getenv('COMMUTERRAIL_PARIS_API_KEY') ?: '');

echo json_encode([
    'boston_api_key' => $bostonKey,
    'paris_api_key' => $parisKey,
]);
