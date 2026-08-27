import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.reactive.function.client.WebClient;

class SpringHttpclientSsrf {

    public String badRestTemplate(@RequestParam String url, RestTemplate rt) {
        // ruleid: spring-httpclient-ssrf
        return rt.getForObject(url, String.class);
    }

    public Object badExchange(@PathVariable String url, RestTemplate rt) {
        // ruleid: spring-httpclient-ssrf
        return rt.exchange(url, null, null, String.class);
    }

    public Object badWebClient(@RequestBody String url, WebClient.Builder builder) {
        // ruleid: spring-httpclient-ssrf
        return builder.build().get().uri(url).retrieve().bodyToMono(String.class);
    }

    public Response badOkHttp(@RequestParam String url, OkHttpClient client) throws Exception {
        Request req = new Request.Builder()
                // ruleid: spring-httpclient-ssrf
                .url(url)
                .build();
        // ruleid: spring-httpclient-ssrf
        return client.newCall(req).execute();
    }

    public String badHttpClient(@RequestParam String url, HttpClient client) throws Exception {
        HttpRequest request = HttpRequest.newBuilder()
                // ruleid: spring-httpclient-ssrf
                .uri(URI.create(url))
                .build();
        // ruleid: spring-httpclient-ssrf
        HttpResponse<String> resp = client.send(request, HttpResponse.BodyHandlers.ofString());
        return resp.body();
    }

    public String safeRestTemplate(@RequestParam String path, RestTemplate rt) {
        String url = "https://api.example.com/v1/" + path.replaceAll("[^a-zA-Z0-9/_-]", "");
        // Still flagged if path taints URL — use constant host allowlist in real code.
        // ok: spring-httpclient-ssrf
        return rt.getForObject("https://api.example.com/health", String.class);
    }
}
