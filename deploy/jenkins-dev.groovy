// Argus 本地开发 Jenkins 初始化：创建 argus 管理用户（幂等）。
// API Token 在启动后经 UI 端点生成（见 deploy/README.md 的「本地 Jenkins」小节）。
import jenkins.model.*
import hudson.model.*
import hudson.security.*

def jenkins = Jenkins.get()
def password = System.getenv('ARGUS_JENKINS_PASSWORD') ?: 'argus-local-demo'
if (!(jenkins.getSecurityRealm() instanceof HudsonPrivateSecurityRealm)) {
    def realm = new HudsonPrivateSecurityRealm(false)
    realm.createAccount('argus', password)
    jenkins.setSecurityRealm(realm)
}
def strategy = new FullControlOnceLoggedInAuthorizationStrategy()
strategy.setAllowAnonymousRead(false)
jenkins.setAuthorizationStrategy(strategy)
if (jenkins.getNumExecutors() < 6) {
    jenkins.setNumExecutors(6)
}
jenkins.save()
println 'ARGUS-JENKINS-INIT: security realm ready'
